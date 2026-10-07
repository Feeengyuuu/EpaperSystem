from __future__ import annotations

import base64
from datetime import datetime, timezone
from io import StringIO
from pathlib import Path
import re
import sys

import pytest
from PIL import Image


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from web_portal.preview import (  # noqa: E402
    PreviewConfigurationError,
    build_preview_app,
    main,
)
import web_portal.preview as preview_module  # noqa: E402


NOW = datetime(2026, 8, 2, 18, 30, tzinfo=timezone.utc)
PASSWORD = "correct horse battery staple"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4////fwAJ+wP9KobjigAAAABJRU5ErkJggg=="
)


def _write_png(path: Path) -> Path:
    path.write_bytes(PNG)
    return path


def _write_plugin_png(path: Path, *, size=(37, 19), color=(22, 78, 91)) -> Path:
    Image.new("RGB", size, color).save(path, format="PNG")
    return path


def _showcase_plugin_images(tmp_path):
    return {
        plugin_id: _write_plugin_png(
            tmp_path / f"{plugin_id}.png",
            size=(800, 480),
            color=color,
        )
        for plugin_id, color in {
            "sports_dashboard": (239, 248, 226),
            "live_radar": (245, 245, 245),
            "steam_charts": (255, 255, 255),
            "stocktracker": (255, 247, 211),
            "ticketmaster_events": (211, 230, 224),
        }.items()
    }


def _login(client):
    page = client.get("/login", base_url="http://127.0.0.1:8787")
    assert page.status_code == 200
    match = re.search(r'name="csrf_token" value="([^"]+)"', page.get_data(as_text=True))
    assert match is not None
    csrf_token = match.group(1)
    return client.post(
        "/login",
        base_url="http://127.0.0.1:8787",
        data={
            "username": "admin",
            "password": PASSWORD,
            "csrf_token": csrf_token,
        },
    )


def test_preview_app_uses_real_ledger_auth_and_exact_weather_raster(tmp_path):
    image_path = _write_png(tmp_path / "weather.png")
    state_root = tmp_path / "state"

    app = build_preview_app(
        image_path,
        state_root,
        PASSWORD,
        clock=lambda: NOW,
    )

    assert app.config["SESSION_COOKIE_NAME"] == "epaper_portal_preview"
    assert app.config["SESSION_COOKIE_SECURE"] is False
    assert app.config["TRUSTED_HOSTS"] == ["127.0.0.1", "localhost"]
    assert app.extensions["web_portal_credentials"].verify_admin_password(PASSWORD)
    assert not (state_root / "security" / "bootstrap_admin.token").exists()

    publications = app.extensions["web_portal_publications"]
    assert publications._module._read_only is True
    catalog = publications.list_publications()
    assert catalog["active_playlist"] == "local-preview"
    assert [item["slug"] for item in catalog["publications"]] == ["local-weather-preview"]
    publication = publications.get_publication("local-weather-preview")
    assert publication["plugin"] == "weather"
    assert publication["title"] == "当地天气"
    assert publication["kind"] == "legacy_png"
    assert publication["payload"] == {
        "alt": "当地天气原版插件画面",
        "data_mode": "cache",
        "height": 1,
        "source_label": "Weather 原版画面",
        "source_updated_at": datetime.fromtimestamp(image_path.stat().st_mtime, timezone.utc).isoformat(),
        "width": 1,
    }
    assert publications.get_asset(publication["asset_id"])["body"] == PNG

    client = app.test_client()
    login = _login(client)
    assert login.status_code == 302
    cookies = login.headers.getlist("Set-Cookie")
    assert any(value.startswith("epaper_portal_preview=") for value in cookies)
    assert all("__Host-" not in value for value in cookies)
    assert all("Secure" not in value for value in cookies)
    assert all("HttpOnly" in value for value in cookies)
    assert all("SameSite=Lax" in value for value in cookies)
    page = client.get("/", base_url="http://127.0.0.1:8787")
    page_text = page.get_data(as_text=True)
    assert page.status_code == 200
    assert "当地天气" in page_text
    assert "当地时间" in page_text
    assert "原版插件画面" in page_text

    rejected_host = app.test_client().get(
        "/healthz",
        base_url="http://attacker.invalid:8787",
    )
    assert rejected_host.status_code == 400
    rejected_remote_peer = app.test_client().get(
        "/healthz",
        base_url="http://127.0.0.1:8787",
        environ_overrides={"REMOTE_ADDR": "203.0.113.10"},
    )
    assert rejected_remote_peer.status_code == 400


def test_showcase_non_weather_detail_and_play_use_the_exact_original_raster(tmp_path):
    steam_path = _write_plugin_png(tmp_path / "steam.png")
    steam_body = steam_path.read_bytes()
    app = build_preview_app(
        _write_png(tmp_path / "weather.png"),
        tmp_path / "state",
        PASSWORD,
        clock=lambda: NOW,
        showcase=True,
        plugin_images={"steam_charts": steam_path},
    )

    client = app.test_client()
    assert _login(client).status_code == 302
    catalog = client.get(
        "/api/publications",
        base_url="http://127.0.0.1:8787",
    ).get_json()
    by_slug = {item["slug"]: item for item in catalog["publications"]}
    steam = by_slug["local-steam-preview"]
    assert steam["plugin"] == "steam_charts"
    assert steam["kind"] == "legacy_png"
    assert steam["payload"]["width"] == 37
    assert steam["payload"]["height"] == 19

    asset_url = f"/assets/{steam['asset_id']}"
    asset = client.get(asset_url, base_url="http://127.0.0.1:8787")
    assert asset.status_code == 200
    assert asset.data == steam_body

    detail = client.get(
        "/p/local-steam-preview",
        base_url="http://127.0.0.1:8787",
    ).get_data(as_text=True)
    play = client.get(
        "/play?playlist=showcase-overview",
        base_url="http://127.0.0.1:8787",
    ).get_data(as_text=True)
    assert f'src="{asset_url}"' in detail
    assert f'src="{asset_url}"' in play
    assert 'width="37"' in detail and 'height="19"' in detail
    assert 'width="37"' in play and 'height="19"' in play
    assert "原版插件画面" in detail
    assert "native-edition" not in detail


def test_showcase_publishes_three_playlists_of_exact_original_plugin_frames(tmp_path):
    plugin_images = _showcase_plugin_images(tmp_path)
    expected_bodies = {plugin_id: path.read_bytes() for plugin_id, path in plugin_images.items()}
    app = build_preview_app(
        _write_png(tmp_path / "weather.png"),
        tmp_path / "state",
        PASSWORD,
        clock=lambda: NOW,
        showcase=True,
        plugin_images=plugin_images,
    )

    client = app.test_client()
    assert _login(client).status_code == 302
    api_response = client.get(
        "/api/publications",
        base_url="http://127.0.0.1:8787",
    )
    assert api_response.status_code == 200
    catalog = api_response.get_json()
    assert catalog["active_playlist"] == "showcase-overview"
    assert [item["slug"] for item in catalog["playlists"]] == [
        "showcase-overview",
        "showcase-interests",
        "showcase-planning",
    ]
    playlists = {item["slug"]: item["publication_slugs"] for item in catalog["playlists"]}
    assert playlists == {
        "showcase-overview": [
            "local-weather-preview",
            "local-sports-preview",
            "local-live-radar-preview",
            "local-steam-preview",
            "local-stock-preview",
            "local-events-preview",
        ],
        "showcase-interests": [
            "local-live-radar-preview",
            "local-steam-preview",
        ],
        "showcase-planning": [
            "local-stock-preview",
            "local-events-preview",
        ],
    }
    expected_slugs = [
        "local-weather-preview",
        "local-sports-preview",
        "local-live-radar-preview",
        "local-steam-preview",
        "local-stock-preview",
        "local-events-preview",
    ]
    assert [item["slug"] for item in catalog["publications"]] == expected_slugs
    by_slug = {item["slug"]: item for item in catalog["publications"]}
    assert {item["kind"] for item in by_slug.values()} == {"legacy_png"}
    assert {item["data_mode"] for item in by_slug.values()} == {"cache"}
    assert {item["plugin"] for slug, item in by_slug.items() if slug != "local-weather-preview"} == set(
        plugin_images
    )

    for slug, publication in by_slug.items():
        asset = client.get(
            f"/assets/{publication['asset_id']}",
            base_url="http://127.0.0.1:8787",
        )
        assert asset.status_code == 200
        if slug == "local-weather-preview":
            assert asset.data == PNG
        else:
            assert asset.data == expected_bodies[publication["plugin"]]
            assert publication["payload"]["width"] == 800
            assert publication["payload"]["height"] == 480
        page = client.get(
            f"/p/{slug}",
            base_url="http://127.0.0.1:8787",
        ).get_data(as_text=True)
        assert "原版插件画面" in page
        assert "native-edition" not in page

    play = client.get(
        "/play?playlist=showcase-overview",
        base_url="http://127.0.0.1:8787",
    )
    assert play.status_code == 200
    play_html = play.get_data(as_text=True)
    assert len(re.findall(r'<article class="play-slide[^"]*" data-slide', play_html)) == 6
    assert "native-edition" not in play_html


def test_preview_rejects_invalid_image_and_password_before_creating_state(tmp_path):
    invalid_image = tmp_path / "weather.png"
    invalid_image.write_bytes(b"not-a-png")

    with pytest.raises(PreviewConfigurationError, match="PNG"):
        build_preview_app(invalid_image, tmp_path / "invalid-image-state", PASSWORD)
    with pytest.raises(PreviewConfigurationError, match="12"):
        build_preview_app(
            _write_png(tmp_path / "valid.png"),
            tmp_path / "invalid-password-state",
            "too short",
        )

    assert not (tmp_path / "invalid-image-state").exists()
    assert not (tmp_path / "invalid-password-state").exists()


def test_preview_rejects_unsafe_plugin_images_before_creating_state(tmp_path):
    valid_image = _write_plugin_png(tmp_path / "valid-plugin.png")
    invalid_image = tmp_path / "invalid-plugin.png"
    invalid_image.write_bytes(b"not-a-png")

    with pytest.raises(PreviewConfigurationError, match="unsupported"):
        build_preview_app(
            _write_png(tmp_path / "weather.png"),
            tmp_path / "unsupported-state",
            PASSWORD,
            clock=lambda: NOW,
            showcase=True,
            plugin_images={"mini_weather": valid_image},
        )
    with pytest.raises(PreviewConfigurationError, match="showcase"):
        build_preview_app(
            _write_png(tmp_path / "weather-without-showcase.png"),
            tmp_path / "without-showcase-state",
            PASSWORD,
            clock=lambda: NOW,
            plugin_images={"steam_charts": valid_image},
        )
    with pytest.raises(PreviewConfigurationError, match="PNG"):
        build_preview_app(
            _write_png(tmp_path / "weather-with-invalid-plugin.png"),
            tmp_path / "invalid-plugin-state",
            PASSWORD,
            clock=lambda: NOW,
            showcase=True,
            plugin_images={"steam_charts": invalid_image},
        )

    assert not (tmp_path / "unsupported-state").exists()
    assert not (tmp_path / "without-showcase-state").exists()
    assert not (tmp_path / "invalid-plugin-state").exists()


def test_preview_build_failure_removes_partial_publication_and_credentials(tmp_path, monkeypatch):
    state_root = tmp_path / "failed-state"

    def fail_bootstrap(_self, _token, _password):
        raise RuntimeError("credential setup failed")

    monkeypatch.setattr(
        preview_module.CredentialStore,
        "consume_bootstrap_token",
        fail_bootstrap,
    )

    with pytest.raises(RuntimeError, match="credential setup failed"):
        build_preview_app(
            _write_png(tmp_path / "weather.png"),
            state_root,
            PASSWORD,
        )

    assert state_root.is_dir()
    assert list(state_root.iterdir()) == []


def test_preview_cli_binds_only_loopback_and_does_not_echo_password(tmp_path):
    image_path = _write_png(tmp_path / "weather.png")
    password_file = tmp_path / "password.txt"
    password_file.write_text(PASSWORD + "\n", encoding="utf-8")
    output = StringIO()
    observed = {}
    temporary_state_root = None

    def serve(app, **options):
        nonlocal temporary_state_root
        observed.update(options)
        temporary_state_root = app.extensions["web_portal_publications"]._module._store.root.parent
        assert temporary_state_root.is_dir()
        response = app.test_client().get(
            "/healthz",
            base_url="http://127.0.0.1:43210",
        )
        assert response.status_code == 200

    result = main(
        [
            "--weather-image",
            str(image_path),
            "--password-file",
            str(password_file),
            "--port",
            "43210",
        ],
        serve=serve,
        output=output,
    )

    assert result == 0
    assert observed == {"host": "127.0.0.1", "port": 43210, "threads": 4}
    assert "http://127.0.0.1:43210" in output.getvalue()
    assert PASSWORD not in output.getvalue()
    assert temporary_state_root is not None
    assert not temporary_state_root.exists()


def test_preview_cli_showcase_flag_exposes_complete_original_frame_catalog(tmp_path):
    image_path = _write_png(tmp_path / "weather.png")
    plugin_images = _showcase_plugin_images(tmp_path)
    password_file = tmp_path / "password.txt"
    password_file.write_text(PASSWORD, encoding="utf-8")
    observed = {}

    def serve(app, **_options):
        client = app.test_client()
        assert _login(client).status_code == 302
        response = client.get(
            "/api/publications",
            base_url="http://127.0.0.1:43213",
        )
        assert response.status_code == 200
        observed.update(response.get_json())

    arguments = [
        "--weather-image",
        str(image_path),
        "--password-file",
        str(password_file),
        "--port",
        "43213",
        "--showcase",
    ]
    for plugin_id, path in plugin_images.items():
        arguments.extend(["--plugin-image", f"{plugin_id}={path}"])
    result = main(
        arguments,
        serve=serve,
        output=StringIO(),
    )

    assert result == 0
    assert observed["active_playlist"] == "showcase-overview"
    assert [item["slug"] for item in observed["publications"]] == [
        "local-weather-preview",
        "local-sports-preview",
        "local-live-radar-preview",
        "local-steam-preview",
        "local-stock-preview",
        "local-events-preview",
    ]
    assert {item["kind"] for item in observed["publications"]} == {"legacy_png"}


def test_preview_cli_accepts_a_unicode_interactive_password(tmp_path):
    image_path = _write_png(tmp_path / "weather.png")
    password = "天气预览密码安全一二三四"
    answers = iter((password, password))
    observed = {}

    def serve(app, **options):
        observed.update(options)
        assert app.extensions["web_portal_credentials"].verify_admin_password(password)

    result = main(
        ["--weather-image", str(image_path), "--port", "43211"],
        serve=serve,
        password_reader=lambda _prompt: next(answers),
        output=StringIO(),
    )

    assert result == 0
    assert observed["host"] == "127.0.0.1"


def test_preview_cli_reports_bind_failure_after_cleaning_state(tmp_path, capsys):
    image_path = _write_png(tmp_path / "weather.png")
    password_file = tmp_path / "password.txt"
    password_file.write_text(PASSWORD, encoding="utf-8")
    temporary_state_root = None

    def failed_serve(app, **_options):
        nonlocal temporary_state_root
        temporary_state_root = app.extensions["web_portal_publications"]._module._store.root.parent
        raise OSError(10048, "address already in use")

    with pytest.raises(SystemExit) as error:
        main(
            [
                "--weather-image",
                str(image_path),
                "--password-file",
                str(password_file),
                "--port",
                "43212",
            ],
            serve=failed_serve,
            output=StringIO(),
        )

    captured = capsys.readouterr()
    assert error.value.code == 2
    assert "could not bind to 127.0.0.1:43212" in captured.err
    assert "Traceback" not in captured.err
    assert PASSWORD not in captured.err
    assert temporary_state_root is not None
    assert not temporary_state_root.exists()

