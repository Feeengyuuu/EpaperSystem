"""Loopback-only preview of the real portal with a static Weather raster."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import gc
import getpass
import hmac
from io import BytesIO
from ipaddress import ip_address
from pathlib import Path
import secrets
import shutil
import sys
from tempfile import TemporaryDirectory
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from PIL import Image, UnidentifiedImageError
from werkzeug.wrappers import Response

from publication import (
    AssetInput,
    PlaylistEntry,
    PlaylistSpec,
    ProducerBatch,
    PublicationDraft,
    PublicationModule,
    TimeWindow,
    WorkBudget,
)
from publication_producer import build_default_adapter_registry
from security.credentials import CredentialStore, MAX_PASSWORD_LENGTH, MIN_PASSWORD_LENGTH

from .app import create_web_app
from .factories import LedgerPublicationSource
from .preview_showcase import (
    RasterPreview,
    SHOWCASE_PLUGIN_IDS,
    build_showcase_batch,
)


LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8787
DEFAULT_TIMEZONE = "America/Los_Angeles"
MAX_RASTER_BYTES = 24 * 1024 * 1024
MAX_IMAGE_PIXELS = 24 * 1024 * 1024
MAX_PASSWORD_FILE_BYTES = 4 * MAX_PASSWORD_LENGTH + 2


class PreviewConfigurationError(RuntimeError):
    """Raised before the local preview binds a socket."""


class _StaticWeatherProducer:
    def __init__(self, batch: ProducerBatch) -> None:
        self._batch = batch

    def collect_due(self, _budget: WorkBudget) -> ProducerBatch:
        return self._batch


class _LoopbackHostMiddleware:
    """Reject non-loopback Host headers before Flask builds a URL adapter."""

    def __init__(self, application) -> None:
        self._application = application

    @staticmethod
    def _allowed(host_header: str) -> bool:
        authority = str(host_header or "").strip().casefold()
        if authority in {"127.0.0.1", "localhost"}:
            return True
        for host in ("127.0.0.1", "localhost"):
            prefix = f"{host}:"
            if authority.startswith(prefix):
                port = authority[len(prefix) :]
                return port.isdigit() and 1 <= int(port) <= 65535
        return False

    @staticmethod
    def _loopback_peer(remote_addr: str) -> bool:
        try:
            return ip_address(str(remote_addr or "").strip()).is_loopback
        except ValueError:
            return False

    def __call__(self, environ, start_response):
        if not self._allowed(environ.get("HTTP_HOST", "")) or not self._loopback_peer(environ.get("REMOTE_ADDR", "")):
            return Response("Bad Request", status=400, content_type="text/plain")(environ, start_response)
        return self._application(environ, start_response)


def _aware_now(clock) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None:
        raise PreviewConfigurationError("preview clock must return a timezone-aware datetime")
    return value.astimezone(timezone.utc)


def _validated_timezone(timezone_name: str) -> str:
    value = str(timezone_name or "").strip()
    try:
        ZoneInfo(value)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise PreviewConfigurationError("timezone must be a valid IANA timezone") from error
    return value


def _validated_password(password: str) -> str:
    if not isinstance(password, str):
        raise PreviewConfigurationError("password must be text")
    if "\x00" in password:
        raise PreviewConfigurationError("password contains an invalid character")
    if "\r" in password or "\n" in password:
        raise PreviewConfigurationError("password must be a single line")
    if not MIN_PASSWORD_LENGTH <= len(password) <= MAX_PASSWORD_LENGTH:
        raise PreviewConfigurationError(
            f"password must contain {MIN_PASSWORD_LENGTH} to {MAX_PASSWORD_LENGTH} characters"
        )
    return password


def _validated_png(path: str | Path) -> tuple[bytes, int, int, datetime]:
    image_path = Path(path).expanduser()
    try:
        if not image_path.is_file():
            raise OSError
        source_updated_at = datetime.fromtimestamp(image_path.stat().st_mtime, timezone.utc)
        with image_path.open("rb") as handle:
            body = handle.read(MAX_RASTER_BYTES + 1)
    except OSError as error:
        raise PreviewConfigurationError("plugin PNG is not a readable file") from error
    if not 1 <= len(body) <= MAX_RASTER_BYTES:
        raise PreviewConfigurationError("plugin PNG exceeds the supported file-size limit")
    try:
        with Image.open(BytesIO(body)) as image:
            if image.format != "PNG":
                raise PreviewConfigurationError("plugin image must be a valid PNG")
            width, height = image.size
            image.verify()
    except PreviewConfigurationError:
        raise
    except (Image.DecompressionBombError, OSError, SyntaxError, UnidentifiedImageError, ValueError) as error:
        raise PreviewConfigurationError("plugin image must be a valid PNG") from error
    if width < 1 or height < 1 or width * height > MAX_IMAGE_PIXELS:
        raise PreviewConfigurationError("plugin PNG exceeds the supported pixel limit")
    return body, width, height, source_updated_at


def _validated_plugin_images(value) -> dict[str, RasterPreview]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise PreviewConfigurationError("plugin images must be a mapping")
    previews: dict[str, RasterPreview] = {}
    for raw_plugin_id, path in value.items():
        plugin_id = str(raw_plugin_id or "").strip()
        if plugin_id not in SHOWCASE_PLUGIN_IDS:
            raise PreviewConfigurationError("showcase plugin image is unsupported")
        body, width, height, source_updated_at = _validated_png(path)
        previews[plugin_id] = RasterPreview(
            body=body,
            width=width,
            height=height,
            source_updated_at=source_updated_at,
        )
    return previews


def _prepare_empty_state_root(path: str | Path) -> Path:
    root = Path(path).expanduser()
    if root.exists():
        if root.is_symlink() or not root.is_dir():
            raise PreviewConfigurationError("preview state root must be a regular directory")
        try:
            has_entries = next(root.iterdir(), None) is not None
        except OSError as error:
            raise PreviewConfigurationError("preview state root is not readable") from error
        if has_entries:
            raise PreviewConfigurationError("preview state root must be empty")
    else:
        try:
            root.mkdir(parents=True)
        except OSError as error:
            raise PreviewConfigurationError("preview state root could not be created") from error
    return root


def _preview_batch(
    body: bytes,
    width: int,
    height: int,
    generated_at: datetime,
    source_updated_at: datetime,
) -> ProducerBatch:
    instance_uuid = "local-weather-preview"
    return ProducerBatch(
        catalog_revision=1,
        active_playlist="local-preview",
        playlists=(
            PlaylistSpec(
                slug="local-preview",
                title="本地预览",
                window=TimeWindow("00:00", "24:00"),
                entries=(
                    PlaylistEntry(
                        instance_uuid=instance_uuid,
                        title="当地天气",
                        plugin_id="weather",
                        order=0,
                    ),
                ),
            ),
        ),
        drafts=(
            PublicationDraft(
                instance_uuid=instance_uuid,
                plugin_id="weather",
                title="当地天气",
                settings_revision=1,
                source_revision=1,
                generated_at=generated_at,
                fresh_until=generated_at + timedelta(hours=6),
                stale_until=generated_at + timedelta(days=7),
                snapshot={
                    "rasterMetadata": {
                        "alt": "当地天气原版插件画面",
                        "width": width,
                        "height": height,
                        "data_mode": "cache",
                        "source_label": "Weather 原版画面",
                        "source_updated_at": source_updated_at.isoformat(),
                    }
                },
                source_assets=(AssetInput("raster", "image/png", body),),
            ),
        ),
    )


def build_preview_app(
    weather_image: str | Path,
    state_root: str | Path,
    admin_password: str,
    *,
    timezone_name: str = DEFAULT_TIMEZONE,
    clock=None,
    showcase: bool = False,
    plugin_images=None,
):
    """Build the formal portal around exact, local plugin PNGs.

    Weather is required and showcase plugin rasters are optional. Every input
    is validated before any state is created. Publication uses the normal
    ledger and adapter registry; the web app receives a newly opened read-only
    ledger reader after the publication transaction completes.
    """

    body, width, height, weather_updated_at = _validated_png(weather_image)
    password = _validated_password(admin_password)
    timezone_name = _validated_timezone(timezone_name)
    clock = clock or (lambda: datetime.now(timezone.utc))
    now = _aware_now(clock)
    if plugin_images and not showcase:
        raise PreviewConfigurationError("plugin images require showcase mode")
    previews = _validated_plugin_images(plugin_images)
    root = _prepare_empty_state_root(state_root)

    publication_root = root / "publication"
    security_root = root / "security"
    writer = None
    credentials = None
    publications = None
    app = None
    try:
        batch = _preview_batch(body, width, height, now, weather_updated_at)
        if showcase:
            batch = build_showcase_batch(batch.drafts[0], now, previews)
        writer = PublicationModule(
            publication_root,
            producer=_StaticWeatherProducer(batch),
            adapters=build_default_adapter_registry(),
            clock=clock,
            timezone_name=timezone_name,
        )
        expected_publications = len(batch.drafts)
        report = writer.publish_due(WorkBudget(max_items=expected_publications))
        if report.published != expected_publications or not report.catalog_updated:
            raise PreviewConfigurationError("Weather preview could not be published")

        credentials = CredentialStore(security_root)
        bootstrap_token = credentials.create_bootstrap_token()
        credentials.consume_bootstrap_token(bootstrap_token, password)

        publications = LedgerPublicationSource(
            PublicationModule(
                publication_root,
                read_only=True,
                clock=clock,
                timezone_name=timezone_name,
            )
        )
        app = create_web_app(
            publications,
            credentials,
            secrets.token_urlsafe(48),
            {
                "PORTAL_TIMEZONE": timezone_name,
                "SESSION_COOKIE_NAME": "epaper_portal_preview",
                "SESSION_COOKIE_SECURE": False,
                "TRUSTED_HOSTS": ["127.0.0.1", "localhost"],
            },
        )
    except Exception:
        app = None
        publications = None
        credentials = None
        writer = None
        gc.collect()
        shutil.rmtree(publication_root, ignore_errors=True)
        shutil.rmtree(security_root, ignore_errors=True)
        raise
    app.wsgi_app = _LoopbackHostMiddleware(app.wsgi_app)
    return app


def _port(value: str) -> int:
    try:
        port = int(value)
    except (TypeError, ValueError) as error:
        raise argparse.ArgumentTypeError("port must be an integer") from error
    if not 1 <= port <= 65535:
        raise argparse.ArgumentTypeError("port must be between 1 and 65535")
    return port


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preview the formal EpaperSystem portal locally with exact original plugin PNGs."
    )
    parser.add_argument("--weather-image", required=True, type=Path, help="Path to the original Weather PNG")
    parser.add_argument("--port", type=_port, default=DEFAULT_PORT, help=f"Loopback port (default: {DEFAULT_PORT})")
    parser.add_argument(
        "--timezone",
        default=DEFAULT_TIMEZONE,
        help=f"IANA timezone used for local-time labels (default: {DEFAULT_TIMEZONE})",
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        help="Automation only: UTF-8 file containing the preview admin password",
    )
    parser.add_argument(
        "--showcase",
        action="store_true",
        help="Build the three showcase playlists from explicitly supplied original plugin PNGs",
    )
    parser.add_argument(
        "--plugin-image",
        action="append",
        default=[],
        metavar="PLUGIN_ID=PNG",
        help="Exact cached plugin PNG to include in the showcase (repeatable)",
    )
    return parser


def _password_from_file(path: Path) -> str:
    try:
        if not path.is_file():
            raise OSError
        with path.open("rb") as handle:
            encoded = handle.read(MAX_PASSWORD_FILE_BYTES + 1)
        if len(encoded) > MAX_PASSWORD_FILE_BYTES:
            raise OSError
        password = encoded.decode("utf-8").rstrip("\r\n")
    except (OSError, UnicodeError) as error:
        raise PreviewConfigurationError("password file is unreadable or too large") from error
    return _validated_password(password)


def _plugin_images(values) -> dict[str, Path]:
    images: dict[str, Path] = {}
    for value in values:
        plugin_id, separator, raw_path = str(value or "").partition("=")
        plugin_id = plugin_id.strip()
        if not separator or not raw_path.strip() or plugin_id not in SHOWCASE_PLUGIN_IDS:
            raise PreviewConfigurationError("plugin image must use a supported PLUGIN_ID=PNG value")
        if plugin_id in images:
            raise PreviewConfigurationError("plugin image was provided more than once")
        images[plugin_id] = Path(raw_path.strip())
    return images


def _interactive_password(password_reader) -> str:
    first = password_reader(f"本地预览管理员密码（至少 {MIN_PASSWORD_LENGTH} 个字符）: ")
    second = password_reader("再次输入密码: ")
    if not isinstance(first, str) or not isinstance(second, str):
        raise PreviewConfigurationError("password must be text")
    if not hmac.compare_digest(first.encode("utf-8"), second.encode("utf-8")):
        raise PreviewConfigurationError("the two passwords do not match")
    return _validated_password(first)


def main(argv=None, *, serve=None, password_reader=None, output=None) -> int:
    """Run a temporary, loopback-only preview until interrupted."""

    parser = _parser()
    args = parser.parse_args(argv)
    password_reader = password_reader or getpass.getpass
    output = output or sys.stdout
    try:
        password = (
            _password_from_file(args.password_file)
            if args.password_file is not None
            else _interactive_password(password_reader)
        )
        plugin_images = _plugin_images(args.plugin_image)
        with TemporaryDirectory(prefix="epaper-portal-preview-") as temporary_root:
            app = None
            try:
                app = build_preview_app(
                    args.weather_image,
                    temporary_root,
                    password,
                    timezone_name=args.timezone,
                    showcase=args.showcase,
                    plugin_images=plugin_images,
                )
                if serve is None:
                    try:
                        from waitress import serve as waitress_serve
                    except ImportError as error:
                        raise PreviewConfigurationError("waitress is required for local preview") from error
                    serve = waitress_serve
                url = f"http://{LOOPBACK_HOST}:{args.port}"
                print(f"本地站点预览：{url}", file=output)
                print("登录用户名：admin", file=output)
                print("仅监听本机；按 Ctrl+C 停止并清理临时数据。", file=output)
                try:
                    serve(app, host=LOOPBACK_HOST, port=args.port, threads=4)
                except KeyboardInterrupt:
                    print("本地预览已停止。", file=output)
                except OSError as error:
                    raise PreviewConfigurationError(
                        f"preview server could not bind to {LOOPBACK_HOST}:{args.port}"
                    ) from error
            finally:
                app = None
                # Flask route closures and sqlite objects can form cycles. On
                # Windows they must be collected before TemporaryDirectory
                # can remove the ledger file.
                gc.collect()
    except PreviewConfigurationError as error:
        parser.error(str(error))
    return 0


__all__ = [
    "PreviewConfigurationError",
    "build_preview_app",
    "main",
]


if __name__ == "__main__":
    raise SystemExit(main())
