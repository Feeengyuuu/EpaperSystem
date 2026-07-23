import sys
import json
from io import BytesIO
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
import requests
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from plugins.ai_image import ai_image as ai_image_module  # noqa: E402
from plugins.ai_image.ai_image import AIImage  # noqa: E402
from plugins.apod import apod as apod_module  # noqa: E402
from plugins.apod.apod import Apod  # noqa: E402
from plugins.base_plugin.render_provenance import SourceProvenance  # noqa: E402
from plugins.image_album import image_album as image_album_module  # noqa: E402
from plugins.image_album.image_album import IMMICH_REQUEST_TIMEOUT_SECONDS, ImageAlbum, ImmichProvider  # noqa: E402
from plugins.unsplash import unsplash as unsplash_module  # noqa: E402
from plugins.unsplash.unsplash import Unsplash  # noqa: E402
from runtime.long_task_executor import InstanceIdentity  # noqa: E402
from runtime.refresh_contracts import (  # noqa: E402
    TaskCancelled,
    TaskDeadlineExceeded,
)


class FakeDeviceConfig:
    def __init__(self, env=None):
        self.env = env or {}

    def load_env_key(self, key):
        return self.env.get(key, "")

    def get_config(self, key=None, default=None):
        values = {
            "orientation": "horizontal",
            "resolution": "800x480",
            "width": 800,
            "height": 480,
        }
        if key is None:
            return values
        return values.get(key, default)

    def get_resolution(self):
        return (800, 480)


class FakeResponse:
    def __init__(self, status_code=200, json_data=None, content=b"", text=""):
        self.status_code = status_code
        self._json_data = json_data
        self.content = content
        self.text = text

    def json(self):
        return self._json_data

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.exceptions.HTTPError(f"HTTP {self.status_code}")


@pytest.fixture(autouse=True)
def apod_runtime_identity(monkeypatch, tmp_path):
    monkeypatch.setenv("INKYPI_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.setenv("INKYPI_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(
        apod_module,
        "current_instance_identity",
        lambda: InstanceIdentity("4f83c7ef-0e5a-4df8-bbe1-62d14f9ef531", 1, 1),
    )


def _network_image_bytes(size=(960, 640)):
    buffer = BytesIO()
    Image.new("RGB", size, (32, 96, 180)).save(buffer, format="JPEG")
    return buffer.getvalue()


def _network_weather():
    return SimpleNamespace(
        scales=SimpleNamespace(state="live"),
        kp=SimpleNamespace(state="live"),
        aggregate_state=SourceProvenance.LIVE,
    )


def test_apod_secret_provider_failure_is_wrapped_without_raw_body_or_query(
    monkeypatch, caplog
):
    secret = "nasa-provider-secret"

    class Http:
        def request_json(self, _method, _url, **_kwargs):
            raise RuntimeError(
                f"provider echoed https://api.nasa.gov/planetary/apod?api_key={secret}"
            )

    with pytest.raises(RuntimeError, match="NASA APOD") as caught:
        apod_module._fetch_apod_record(
            http=Http(),
            api_key=secret,
            requested_date="2026-07-22",
            context=None,
        )

    assert secret not in str(caught.value)
    assert secret not in caplog.text
    assert "api_key=" not in str(caught.value)
    assert "api_key=" not in caplog.text


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_apod_metadata_abort_is_never_wrapped_as_provider_failure(abort_type):
    signal = abort_type("stop metadata")

    class Http:
        def request_json(self, *_args, **_kwargs):
            raise signal

    with pytest.raises(abort_type) as caught:
        apod_module._fetch_apod_record(
            http=Http(),
            api_key="nasa-key",
            requested_date="2026-07-22",
            context=None,
        )

    assert caught.value is signal


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_apod_translation_abort_does_not_fall_through_to_groq(
    monkeypatch,
    abort_type,
):
    signal = abort_type("stop translation")

    class Http:
        def __init__(self):
            self.urls = []

        def request_json(self, _method, url, **_kwargs):
            self.urls.append(url)
            raise signal

    http = Http()
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    plugin = Apod({"id": "apod"})
    paths = apod_module._instance_paths(
        plugin,
        preview_namespace=f"translation-abort-{abort_type.__name__}",
    )

    with pytest.raises(abort_type) as caught:
        apod_module._translate_title(
            title="Abort Translation",
            apod_date="2026-07-22",
            paths=paths,
            device_config=FakeDeviceConfig(
                {
                    "OPEN_AI_SECRET": "openai-key",
                    "GROQ_API_KEY": "groq-key",
                }
            ),
            context=None,
        )

    assert caught.value is signal
    assert http.urls == [apod_module.OPENAI_TRANSLATION_ENDPOINT]
    assert not list(paths.cache.glob("translation-*.json"))


@pytest.mark.parametrize("abort_type", [TaskCancelled, TaskDeadlineExceeded])
def test_apod_media_abort_does_not_try_hd_or_historical_recovery(
    monkeypatch,
    abort_type,
):
    signal = abort_type("stop media")
    standard = "https://media.example.test/cancel-standard.jpg"
    hd = "https://media.example.test/cancel-hd.jpg"

    class Http:
        def __init__(self):
            self.urls = []

        def stream_to_file(self, _method, url, _path, **_kwargs):
            self.urls.append(url)
            raise signal

    http = Http()
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    plugin = Apod({"id": "apod"})
    paths = apod_module._instance_paths(
        plugin,
        preview_namespace=f"media-abort-{abort_type.__name__}",
    )
    record = apod_module.ApodRecord(
        selection_key="selection",
        requested_device_date="2026-07-22",
        date="2026-07-22",
        media_type="image",
        title_en="Abort Media",
        title_zh=None,
        translation_state="pending",
        explanation="",
        copyright=None,
        url=standard,
        hdurl=hd,
        image_url=None,
        image_cache_key=None,
        fetched_at_utc=datetime(2026, 7, 22, tzinfo=timezone.utc),
        source_state="live",
        warning=None,
    )

    with pytest.raises(abort_type) as caught:
        apod_module._resolve_media_blob(
            plugin=plugin,
            record=record,
            paths=paths,
            minimum_size=(432, 299),
            context=None,
        )

    assert caught.value is signal
    assert http.urls == [standard]


def test_apod_weather_abort_does_not_persist_state_render_or_context(monkeypatch):
    media_url = "https://media.example.test/weather-abort.jpg"

    class Http:
        def request_json(self, _method, url, **kwargs):
            requested = kwargs["params"]["date"]
            return SimpleNamespace(
                data={
                    "date": requested,
                    "media_type": "image",
                    "title": "Weather Abort",
                    "url": media_url,
                },
                url=url,
            )

        def stream_to_file(self, *_args, **_kwargs):
            pytest.fail("media must not start after weather cancellation")

    monkeypatch.setattr(apod_module, "get_http_client", lambda: Http())
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            TaskCancelled("weather canceled")
        ),
    )
    monkeypatch.setattr(
        apod_module,
        "render_apod_page",
        lambda **_kwargs: pytest.fail("render must not run"),
    )
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *_args, **_kwargs: pytest.fail("context must not run"),
    )
    plugin = Apod({"id": "apod"})

    with pytest.raises(TaskCancelled):
        plugin.generate_image({}, FakeDeviceConfig({"NASA_SECRET": "nasa-key"}))

    paths = apod_module._instance_paths(plugin)
    assert not (paths.cache / "apod-state.json").exists()


def test_apod_cancellation_after_decode_precedes_state_render_and_context(
    monkeypatch,
):
    media_url = "https://media.example.test/decode-abort.jpg"

    class Context:
        cancelled = False

        def raise_if_cancelled(self):
            if self.cancelled:
                raise TaskCancelled("canceled after decode")

    class Http:
        def request_json(self, _method, url, **kwargs):
            requested = kwargs["params"]["date"]
            return SimpleNamespace(
                data={
                    "date": requested,
                    "media_type": "image",
                    "title": "Decode Abort",
                    "url": media_url,
                },
                url=url,
            )

        def stream_to_file(self, _method, url, path, **_kwargs):
            assert url == media_url
            Path(path).write_bytes(_network_image_bytes())
            return SimpleNamespace(data=Path(path), url=url)

    context = Context()
    real_decode = apod_module._decode_media_blob

    def decode(*, blob_path, photo_size):
        image = real_decode(blob_path=blob_path, photo_size=photo_size)
        context.cancelled = True
        return image

    monkeypatch.setattr(apod_module, "get_http_client", lambda: Http())
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: context)
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: _network_weather(),
    )
    monkeypatch.setattr(apod_module, "_decode_media_blob", decode)
    monkeypatch.setattr(
        apod_module,
        "render_apod_page",
        lambda **_kwargs: pytest.fail("render must not run after cancellation"),
    )
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *_args, **_kwargs: pytest.fail("context must not run"),
    )
    plugin = Apod({"id": "apod"})

    with pytest.raises(TaskCancelled):
        plugin.generate_image({}, FakeDeviceConfig({"NASA_SECRET": "nasa-key"}))

    paths = apod_module._instance_paths(plugin)
    assert not (paths.cache / "apod-state.json").exists()


@pytest.mark.parametrize("failure_layer", ["download", "decode"])
def test_apod_provisional_media_retries_current_each_cadence_reuses_fallback_and_switches_once(
    monkeypatch,
    failure_layer,
):
    current_url = "https://media.example.test/current.jpg"
    fallback_url = "https://media.example.test/fallback.jpg"
    payloads = {
        "2026-07-22": {
            "date": "2026-07-22",
            "media_type": "image",
            "title": "Current APOD",
            "explanation": "Current explanation",
            "copyright": "Current Photographer",
            "url": current_url,
        },
        "2026-07-21": {
            "date": "2026-07-21",
            "media_type": "image",
            "title": "Fallback APOD",
            "explanation": "Fallback explanation",
            "copyright": "Fallback Photographer",
            "url": fallback_url,
        },
    }

    class Http:
        def __init__(self):
            self.apod_dates = []
            self.download_urls = []
            self.current_outcomes = (
                [
                    RuntimeError("current media offline"),
                    RuntimeError("current media still offline"),
                    _network_image_bytes(),
                ]
                if failure_layer == "download"
                else [_network_image_bytes()] * 3
            )

        def request_json(self, method, url, **kwargs):
            assert method == "GET"
            assert url == "https://api.nasa.gov/planetary/apod"
            requested = kwargs["params"]["date"]
            self.apod_dates.append(requested)
            return SimpleNamespace(
                status=200,
                data=dict(payloads[requested]),
                headers={},
                url=url,
            )

        def stream_to_file(self, method, url, path, **_kwargs):
            assert method == "GET"
            self.download_urls.append(url)
            if url == current_url:
                outcome = self.current_outcomes.pop(0)
            else:
                outcome = _network_image_bytes()
            if isinstance(outcome, Exception):
                raise outcome
            Path(path).write_bytes(outcome)
            return SimpleNamespace(status=200, data=Path(path), headers={}, url=url)

    http = Http()
    rendered = []
    real_decode = apod_module._decode_media_blob
    current_digest = apod_module.hashlib.sha256(
        current_url.encode("utf-8")
    ).hexdigest()
    decode_attempts = 0

    def decode_media(*, blob_path, photo_size):
        nonlocal decode_attempts
        if failure_layer == "decode" and current_digest in Path(blob_path).name:
            decode_attempts += 1
            if decode_attempts <= 2:
                raise apod_module.ApodMediaUnavailable(
                    "current media decoder failure"
                )
        return real_decode(blob_path=blob_path, photo_size=photo_size)

    monkeypatch.setattr(apod_module, "get_http_client", lambda: http, raising=False)
    monkeypatch.setattr(apod_module, "_decode_media_blob", decode_media)
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(
        apod_module, "current_task_context", lambda: None, raising=False
    )
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: _network_weather(),
        raising=False,
    )

    def render(**kwargs):
        record = kwargs["apod"]
        rendered.append(
            (record.date, record.title_en, record.copyright, record.warning)
        )
        return Image.new("RGB", (800, 480), (250, 250, 246))

    monkeypatch.setattr(apod_module, "render_apod_page", render, raising=False)
    monkeypatch.setattr(apod_module, "write_context", lambda *_args, **_kwargs: None)

    plugin = Apod({"id": "apod"})
    config = FakeDeviceConfig({"NASA_SECRET": "nasa-key"})

    plugin.generate_image({}, config)
    paths = apod_module._instance_paths(plugin)
    first_state = json.loads(
        (paths.cache / "apod-state.json").read_text(encoding="utf-8")
    )
    selection_state = json.loads(
        (paths.data / "selection.json").read_text(encoding="utf-8")
    )
    current_blob = paths.media / f"{current_digest}.img"
    plugin.generate_image({"forceRefresh": "true"}, config)
    if failure_layer == "decode":
        assert not current_blob.exists()
    plugin.generate_image({"forceRefresh": "true"}, config)
    if failure_layer == "decode":
        assert current_blob.exists()
    downloads_after_current_success = list(http.download_urls)
    plugin.generate_image({"forceRefresh": "true"}, config)
    if failure_layer == "decode":
        assert current_blob.exists()
        assert http.download_urls == downloads_after_current_success

    assert first_state["requested_record"]["date"] == "2026-07-22"
    assert first_state["display_record"]["date"] == "2026-07-21"
    assert first_state["fallback_reason"] == "current_media_unavailable"
    assert first_state["provisional_media"] is True
    assert selection_state["provisional"] is False
    assert http.apod_dates == ["2026-07-22", "2026-07-21"]
    if failure_layer == "download":
        assert http.download_urls == [
            current_url,
            fallback_url,
            current_url,
            current_url,
        ]
    else:
        assert http.download_urls == [
            current_url,
            fallback_url,
            current_url,
            current_url,
        ]
    assert rendered == [
        (
            "2026-07-21",
            "Fallback APOD",
            "Fallback Photographer",
            "LATEST AVAILABLE · APOD 2026-07-21",
        ),
        (
            "2026-07-21",
            "Fallback APOD",
            "Fallback Photographer",
            "LATEST AVAILABLE · APOD 2026-07-21",
        ),
        ("2026-07-22", "Current APOD", "Current Photographer", None),
        ("2026-07-22", "Current APOD", "Current Photographer", None),
    ]


def test_apod_video_fallback_never_crosses_requested_day_minus_seven(
    monkeypatch,
):
    corrupt_url = "https://media.example.test/day-minus-seven-corrupt.jpg"
    forbidden_url = "https://media.example.test/day-minus-eight-valid.jpg"

    class Http:
        def __init__(self):
            self.apod_dates = []
            self.download_urls = []

        def request_json(self, _method, url, **kwargs):
            requested = kwargs["params"]["date"]
            self.apod_dates.append(requested)
            if requested == "2026-07-22" or requested >= "2026-07-16":
                payload = {
                    "date": requested,
                    "media_type": "video",
                    "title": f"Video {requested}",
                }
            elif requested == "2026-07-15":
                payload = {
                    "date": requested,
                    "media_type": "image",
                    "title": "Corrupt day minus seven",
                    "url": corrupt_url,
                }
            else:
                payload = {
                    "date": requested,
                    "media_type": "image",
                    "title": "Forbidden day minus eight",
                    "url": forbidden_url,
                }
            return SimpleNamespace(data=payload, url=url)

        def stream_to_file(self, _method, url, path, **_kwargs):
            self.download_urls.append(url)
            payload = (
                b"not-an-image"
                if url == corrupt_url
                else _network_image_bytes()
            )
            Path(path).write_bytes(payload)
            return SimpleNamespace(data=Path(path), url=url)

    http = Http()
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: _network_weather(),
    )
    monkeypatch.setattr(
        apod_module,
        "render_apod_page",
        lambda **_kwargs: pytest.fail("day minus eight must never render"),
    )
    monkeypatch.setattr(
        apod_module,
        "write_context",
        lambda *_args, **_kwargs: pytest.fail("failed fallback must not publish context"),
    )
    plugin = Apod({"id": "apod"})

    with pytest.raises(
        apod_module.ApodMediaUnavailable,
        match="seven|7|usable|image",
    ):
        plugin.generate_image({}, FakeDeviceConfig({"NASA_SECRET": "nasa-key"}))

    assert http.apod_dates == [
        "2026-07-22",
        "2026-07-21",
        "2026-07-20",
        "2026-07-19",
        "2026-07-18",
        "2026-07-17",
        "2026-07-16",
        "2026-07-15",
    ]
    assert http.download_urls == [corrupt_url]
    assert "2026-07-14" not in http.apod_dates
    assert forbidden_url not in http.download_urls


def test_apod_video_fallback_continues_past_decode_failure_to_newest_usable_day(
    monkeypatch,
):
    corrupt_url = "https://media.example.test/day-minus-one-probe-fails.jpg"
    decode_url = "https://media.example.test/day-minus-two-decode-fails.jpg"
    usable_url = "https://media.example.test/day-minus-three-usable.jpg"

    class Http:
        def __init__(self):
            self.apod_dates = []
            self.download_urls = []

        def request_json(self, _method, url, **kwargs):
            requested = kwargs["params"]["date"]
            self.apod_dates.append(requested)
            if requested == "2026-07-22":
                payload = {
                    "date": requested,
                    "media_type": "video",
                    "title": "Video today",
                }
            else:
                payload = {
                    "date": requested,
                    "media_type": "image",
                    "title": f"Fallback {requested}",
                    "url": {
                        "2026-07-21": corrupt_url,
                        "2026-07-20": decode_url,
                        "2026-07-19": usable_url,
                    }[requested],
                }
            return SimpleNamespace(data=payload, url=url)

        def stream_to_file(self, _method, url, path, **_kwargs):
            self.download_urls.append(url)
            payload = (
                b"not-an-image" if url == corrupt_url else _network_image_bytes()
            )
            Path(path).write_bytes(payload)
            return SimpleNamespace(data=Path(path), url=url)

    http = Http()
    decode_digest = apod_module.hashlib.sha256(
        decode_url.encode("utf-8")
    ).hexdigest()
    real_decode = apod_module._decode_media_blob

    def decode_media(*, blob_path, photo_size):
        if decode_digest in Path(blob_path).name:
            raise apod_module.ApodMediaUnavailable("verified pixels cannot load")
        return real_decode(blob_path=blob_path, photo_size=photo_size)

    rendered = []
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    monkeypatch.setattr(apod_module, "_decode_media_blob", decode_media)
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: _network_weather(),
    )

    def render(**kwargs):
        rendered.append((kwargs["apod"].date, kwargs["apod"].warning))
        return Image.new("RGB", (800, 480), (250, 250, 246))

    monkeypatch.setattr(apod_module, "render_apod_page", render)
    monkeypatch.setattr(apod_module, "write_context", lambda *_args, **_kwargs: None)
    plugin = Apod({"id": "apod"})

    plugin.generate_image({}, FakeDeviceConfig({"NASA_SECRET": "nasa-key"}))

    paths = apod_module._instance_paths(plugin)
    assert http.apod_dates == [
        "2026-07-22",
        "2026-07-21",
        "2026-07-20",
        "2026-07-19",
    ]
    assert http.download_urls == [corrupt_url, decode_url, usable_url]
    assert rendered == [
        ("2026-07-19", "LATEST AVAILABLE · APOD 2026-07-19")
    ]
    assert not (paths.media / f"{decode_digest}.img").exists()



def test_apod_random_mode_advances_persisted_candidates_until_media_decodes(
    monkeypatch,
):
    candidates = (
        "2026-07-18",
        "2026-07-19",
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    )
    unavailable_url = "https://media.example.test/random-unavailable.jpg"
    undecodable_url = "https://media.example.test/random-undecodable.jpg"
    usable_url = "https://media.example.test/random-usable.jpg"

    class Http:
        def __init__(self):
            self.apod_dates = []
            self.download_urls = []

        def request_json(self, method, url, **kwargs):
            assert method == "GET"
            assert url == apod_module.APOD_ENDPOINT
            requested = kwargs["params"]["date"]
            self.apod_dates.append(requested)
            if requested == candidates[0]:
                raise RuntimeError("temporary APOD metadata failure")
            if requested == candidates[1]:
                payload = {
                    "date": requested,
                    "media_type": "video",
                    "title": "Random video",
                }
            else:
                payload = {
                    "date": requested,
                    "media_type": "image",
                    "title": f"Random image {requested}",
                    "explanation": "Random selection regression",
                    "url": {
                        candidates[2]: unavailable_url,
                        candidates[3]: undecodable_url,
                        candidates[4]: usable_url,
                    }[requested],
                }
            return SimpleNamespace(
                status=200,
                data=payload,
                headers={},
                url=url,
            )

        def stream_to_file(self, method, url, path, **_kwargs):
            assert method == "GET"
            self.download_urls.append(url)
            if url == unavailable_url:
                raise RuntimeError("media download unavailable")
            Path(path).write_bytes(_network_image_bytes())
            return SimpleNamespace(status=200, data=Path(path), headers={}, url=url)

    http = Http()
    weather_calls = []
    rendered_dates = []
    real_decode = apod_module._decode_media_blob
    undecodable_digest = apod_module.hashlib.sha256(
        undecodable_url.encode("utf-8")
    ).hexdigest()

    def decode_media(*, blob_path, photo_size):
        if undecodable_digest in Path(blob_path).name:
            raise apod_module.ApodMediaUnavailable("decoder rejected candidate")
        return real_decode(blob_path=blob_path, photo_size=photo_size)

    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(
        apod_module,
        "_random_candidate_dates",
        lambda _device_day, _rng: candidates,
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)
    monkeypatch.setattr(apod_module, "_decode_media_blob", decode_media)

    def refresh_weather(*_args, **_kwargs):
        weather_calls.append("refresh")
        return _network_weather()

    monkeypatch.setattr(apod_module, "refresh_space_weather", refresh_weather)

    def render(**kwargs):
        rendered_dates.append(kwargs["apod"].date)
        return Image.new("RGB", (800, 480), (250, 250, 246))

    monkeypatch.setattr(apod_module, "render_apod_page", render)
    monkeypatch.setattr(apod_module, "write_context", lambda *_args, **_kwargs: None)

    plugin = Apod({"id": "apod"})
    config = FakeDeviceConfig({"NASA_SECRET": "nasa-key"})

    plugin.generate_image({"randomizeApod": "true"}, config)
    paths = apod_module._instance_paths(plugin)
    selection = json.loads(
        (paths.data / "selection.json").read_text(encoding="utf-8")
    )
    state = json.loads(
        (paths.cache / "apod-state.json").read_text(encoding="utf-8")
    )

    assert http.apod_dates == list(candidates)
    assert http.download_urls == [
        unavailable_url,
        undecodable_url,
        usable_url,
    ]
    assert selection["candidate_dates"] == list(candidates)
    assert selection["selected_apod_date"] == candidates[4]
    assert selection["provisional"] is False
    assert state["requested_record"]["date"] == candidates[4]
    assert state["display_record"]["date"] == candidates[4]
    assert rendered_dates == [candidates[4]]

    apod_calls = list(http.apod_dates)
    media_calls = list(http.download_urls)
    plugin.generate_image(
        {"randomizeApod": "true", "forceRefresh": "true"},
        config,
    )

    assert http.apod_dates == apod_calls
    assert http.download_urls == media_calls
    assert rendered_dates == [candidates[4], candidates[4]]
    assert weather_calls == ["refresh", "refresh"]


def test_apod_random_mode_stops_at_five_unique_dates_and_reuses_sequence(
    monkeypatch,
):
    candidates = (
        "2026-07-18",
        "2026-07-19",
        "2026-07-20",
        "2026-07-21",
        "2026-07-22",
    )

    class Http:
        def __init__(self):
            self.apod_dates = []

        def request_json(self, method, url, **kwargs):
            assert method == "GET"
            assert url == apod_module.APOD_ENDPOINT
            requested = kwargs["params"]["date"]
            self.apod_dates.append(requested)
            return SimpleNamespace(
                status=200,
                data={
                    "date": requested,
                    "media_type": "video",
                    "title": "Random video",
                },
                headers={},
                url=url,
            )

        def stream_to_file(self, *_args, **_kwargs):
            pytest.fail("non-image candidates must not start a media download")

    http = Http()
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    monkeypatch.setattr(
        apod_module,
        "_device_day",
        lambda _config: datetime(2026, 7, 22, tzinfo=timezone.utc).date(),
    )
    monkeypatch.setattr(
        apod_module,
        "_random_candidate_dates",
        lambda _device_day, _rng: candidates,
    )
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)

    plugin = Apod({"id": "apod"})
    config = FakeDeviceConfig({"NASA_SECRET": "nasa-key"})

    with pytest.raises(RuntimeError, match="five random dates"):
        plugin.generate_image({"randomizeApod": "true"}, config)
    paths = apod_module._instance_paths(plugin)
    persisted = json.loads(
        (paths.data / "selection.json").read_text(encoding="utf-8")
    )

    with pytest.raises(RuntimeError, match="five random dates"):
        plugin.generate_image(
            {"randomizeApod": "true", "forceRefresh": "true"},
            config,
        )

    assert len(set(candidates)) == 5
    assert http.apod_dates == list(candidates) + list(candidates)
    assert persisted["candidate_dates"] == list(candidates)
    assert persisted["selected_apod_date"] == candidates[0]
    assert persisted["provisional"] is True


def test_apod_today_uses_device_timezone_and_sends_an_explicit_date(
    monkeypatch,
):
    calls = []

    class TimezoneDeviceConfig(FakeDeviceConfig):
        def __init__(self, env, configured_timezone):
            super().__init__(env)
            self.configured_timezone = configured_timezone

        def get_config(self, key=None, default=None):
            if key == "timezone":
                return self.configured_timezone
            return super().get_config(key, default)

    class FrozenDateTime:
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 7, 23, 0, 30, tzinfo=timezone.utc).astimezone(tz)

    class Http:
        def request_json(self, method, url, **kwargs):
            assert method == "GET"
            assert url == apod_module.APOD_ENDPOINT
            params = dict(kwargs["params"])
            calls.append(params)
            requested = params["date"]
            return SimpleNamespace(
                status=200,
                data={
                    "date": requested,
                    "media_type": "image",
                    "title": f"APOD {requested}",
                    "url": f"https://media.example.test/{requested}.jpg",
                },
                headers={},
                url=url,
            )

        def stream_to_file(self, method, url, path, **_kwargs):
            assert method == "GET"
            Path(path).write_bytes(_network_image_bytes())
            return SimpleNamespace(status=200, data=Path(path), headers={}, url=url)

    http = Http()
    monkeypatch.setattr(apod_module, "datetime", FrozenDateTime)
    monkeypatch.setattr(apod_module, "get_http_client", lambda: http)
    monkeypatch.setattr(apod_module, "current_task_context", lambda: None)
    monkeypatch.setattr(
        apod_module,
        "refresh_space_weather",
        lambda *_args, **_kwargs: _network_weather(),
    )
    monkeypatch.setattr(
        apod_module,
        "render_apod_page",
        lambda **_kwargs: Image.new("RGB", (800, 480), (250, 250, 246)),
    )
    monkeypatch.setattr(apod_module, "write_context", lambda *_args, **_kwargs: None)

    plugin = Apod({"id": "apod"})
    plugin.generate_image(
        {},
        TimezoneDeviceConfig(
            {"NASA_SECRET": "nasa-key"},
            "America/Los_Angeles",
        ),
    )
    plugin.generate_image(
        {},
        TimezoneDeviceConfig(
            {"NASA_SECRET": "nasa-key"},
            "not/a-timezone",
        ),
    )

    assert calls == [
        {"api_key": "nasa-key", "date": "2026-07-22"},
        {"api_key": "nasa-key", "date": "2026-07-23"},
    ]


def test_ai_image_download_uses_shared_session_and_http_errors(monkeypatch):
    calls = []

    class Session:
        def get(self, url, timeout=None, stream=False):
            calls.append({"url": url, "timeout": timeout, "stream": stream})
            return FakeResponse(status_code=500, text="server error")

    class Images:
        def generate(self, **_kwargs):
            item = type("ImageItem", (), {"url": "https://example.test/generated.png"})()
            return type("ImageResponse", (), {"data": [item]})()

    monkeypatch.setattr(ai_image_module, "get_http_session", lambda: Session())

    plugin = AIImage({"id": "ai_image"})

    with pytest.raises(requests.exceptions.HTTPError):
        plugin.fetch_image(type("Client", (), {"images": Images()})(), "prompt")

    assert calls == [
        {
            "url": "https://example.test/generated.png",
            "timeout": None,
            "stream": True,
        }
    ]


def test_unsplash_missing_api_key_fails_before_network(monkeypatch):
    monkeypatch.setattr(
        unsplash_module,
        "get_http_session",
        lambda: pytest.fail("Unsplash should not create an HTTP session without an API key"),
    )

    with pytest.raises(RuntimeError, match="Unsplash Access Key"):
        Unsplash({"id": "unsplash"}).generate_image({}, FakeDeviceConfig())


def test_unsplash_request_timeout_is_wrapped(monkeypatch):
    calls = []

    class Session:
        def get(self, url, params=None, timeout=None):
            calls.append({"url": url, "params": dict(params or {}), "timeout": timeout})
            raise requests.exceptions.Timeout("slow response")

    monkeypatch.setattr(unsplash_module, "get_http_session", lambda: Session())

    with pytest.raises(RuntimeError, match="Failed to fetch image from Unsplash API"):
        Unsplash({"id": "unsplash"}).generate_image(
            {"search_query": "mountains"},
            FakeDeviceConfig({"UNSPLASH_ACCESS_KEY": "unsplash-key"}),
        )

    assert calls[0]["url"] == "https://api.unsplash.com/search/photos"
    assert calls[0]["params"]["client_id"] == "unsplash-key"
    assert calls[0]["params"]["query"] == "mountains"
    assert calls[0]["timeout"] == 15


def test_immich_metadata_requests_use_timeout(monkeypatch):
    calls = []

    class Session:
        def get(self, url, headers=None, timeout=None):
            calls.append(("get", url, headers, timeout))
            return FakeResponse(json_data=[{"albumName": "Frame", "id": "album-1"}])

        def post(self, url, json=None, headers=None, timeout=None):
            calls.append(("post", url, json, headers, timeout))
            items = [{"id": "asset-1"}] if json["page"] == 1 else []
            return FakeResponse(json_data={"assets": {"items": items}})

    monkeypatch.setattr(image_album_module, "get_http_session", lambda: Session())

    provider = ImmichProvider("https://immich.example", "immich-key", object())

    assert provider.get_album_id("Frame") == "album-1"
    assert provider.get_assets("album-1") == [{"id": "asset-1"}]
    assert calls[0] == (
        "get",
        "https://immich.example/api/albums",
        {"x-api-key": "immich-key"},
        IMMICH_REQUEST_TIMEOUT_SECONDS,
    )
    assert calls[1][0] == "post"
    assert calls[1][1] == "https://immich.example/api/search/metadata"
    assert calls[1][4] == IMMICH_REQUEST_TIMEOUT_SECONDS


def test_image_album_missing_api_key_fails_before_provider():
    settings = {"albumProvider": "Immich", "url": "https://immich.example", "album": "Frame"}

    with pytest.raises(RuntimeError, match="Immich API Key not configured"):
        ImageAlbum({"id": "image_album"}).generate_image(settings, FakeDeviceConfig())
