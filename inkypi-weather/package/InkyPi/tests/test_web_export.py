import json
import hashlib
import os
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
EXPORTER = REPO_ROOT / "tools" / "export_web_config.py"
SECRET_SCHEMA = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "config" / "secret_schema.json"


def run_export(config_path, output_path, uploads_root):
    return subprocess.run(
        [
            sys.executable,
            str(EXPORTER),
            "--config",
            str(config_path),
            "--output",
            str(output_path),
            "--uploads-root",
            str(uploads_root),
            "--secret-schema",
            str(SECRET_SCHEMA),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )


def test_export_preserves_playlist_order_and_redacts_secrets(tmp_path):
    config = {
        "schema_version": 1,
        "config_revision": 19,
        "timezone": "America/Los_Angeles",
        "playlist_config": {
            "active_playlist": "Night",
            "playlists": [
                {
                    "name": "Day",
                    "start_time": "07:00",
                    "end_time": "19:00",
                    "plugins": [
                        {
                            "plugin_id": "github",
                            "name": "Contributions",
                            "plugin_settings": {
                                "GITHUB_TOKEN": "declared-secret-value",
                                "nested": {"client_secret": "heuristic-secret-value"},
                                "city": "Los Angeles",
                            },
                            "refresh": {"interval": 300, "scheduled": "08:30"},
                            "latest_refresh_time": "2026-08-02T08:00:00-07:00",
                            "instance_uuid": "instance-day",
                            "structural_generation": 4,
                            "settings_revision": 7,
                        }
                    ],
                    "current_plugin_index": 0,
                },
                {
                    "name": "Night",
                    "start_time": "19:00",
                    "end_time": "07:00",
                    "plugins": [],
                    "current_plugin_index": None,
                },
            ],
        },
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["format_version"] == 1
    assert exported["source"] == {"config_revision": 19, "schema_version": 1}
    assert exported["active_playlist"] == "Night"
    assert [playlist["name"] for playlist in exported["playlists"]] == ["Day", "Night"]
    assert exported["playlists"][0]["start_time"] == "07:00"
    assert exported["playlists"][0]["end_time"] == "19:00"
    instance = exported["playlists"][0]["plugins"][0]
    assert instance["instance_uuid"] == "instance-day"
    assert instance["settings_revision"] == 7
    assert instance["refresh"] == {"interval": 300, "scheduled": "08:30"}
    assert instance["plugin_settings"] == {
        "GITHUB_TOKEN": "${GITHUB_SECRET}",
        "nested": {"client_secret": "${CLIENT_SECRET}"},
        "city": "Los Angeles",
    }
    required = json.loads((output_path / "required_secrets.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in required["secrets"]] == [
        "CLIENT_SECRET",
        "GITHUB_SECRET",
    ]
    combined_output = result.stdout + result.stderr
    assert "declared-secret-value" not in combined_output
    assert "heuristic-secret-value" not in combined_output
    assert "declared-secret-value" not in (output_path / "web_config.json").read_text(encoding="utf-8")
    assert "heuristic-secret-value" not in (output_path / "web_config.json").read_text(encoding="utf-8")


def test_export_copies_allowed_upload_as_content_addressed_resource(tmp_path):
    uploads_root = tmp_path / "uploads"
    uploads_root.mkdir()
    source = uploads_root / "family-photo.png"
    payload = b"safe uploaded image"
    source.write_bytes(payload)
    config = {
        "playlist_config": {
            "active_playlist": "Default",
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "image_upload",
                            "name": "Photos",
                            "plugin_settings": {"imageFiles[]": [str(source)]},
                            "refresh": {"interval": 600},
                            "instance_uuid": "image-instance",
                            "settings_revision": 2,
                        }
                    ],
                }
            ],
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, uploads_root)

    assert result.returncode == 0, result.stderr
    digest = hashlib.sha256(payload).hexdigest()
    relative_resource = f"resources/{digest}.png"
    assert (output_path / relative_resource).read_bytes() == payload
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {"imageFiles[]": [relative_resource]}
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    file_paths = [
        "required_secrets.json",
        relative_resource,
        "web_config.json",
    ]
    assert manifest == {
        "files": [
            {
                "path": path,
                "sha256": hashlib.sha256((output_path / path).read_bytes()).hexdigest(),
                "size": (output_path / path).stat().st_size,
            }
            for path in file_paths
        ],
        "format_version": 1,
        "resources": [
            {
                "path": relative_resource,
                "references": [
                    {
                        "instance_uuid": "image-instance",
                        "setting": "imageFiles[]",
                    }
                ],
                "sha256": digest,
                "size": len(payload),
                "status": "available",
            }
        ],
        "warnings": [],
    }
    serialized_bundle = "".join(
        path.read_text(encoding="utf-8")
        for path in (
            output_path / "web_config.json",
            output_path / "manifest.json",
            output_path / "required_secrets.json",
        )
    )
    assert str(source) not in serialized_bundle


def test_export_marks_missing_upload_unavailable_without_leaking_path(tmp_path):
    uploads_root = tmp_path / "uploads"
    uploads_root.mkdir()
    missing = uploads_root / "missing.png"
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "image_upload",
                            "name": "Missing",
                            "plugin_settings": {"imageFiles[]": [str(missing)]},
                            "refresh": {"interval": 600},
                            "instance_uuid": "missing-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, uploads_root)

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {"imageFiles[]": [None]}
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["resources"] == [
        {
            "reason": "missing",
            "references": [
                {
                    "instance_uuid": "missing-instance",
                    "setting": "imageFiles[]",
                }
            ],
            "source_name": "missing.png",
            "status": "unavailable",
        }
    ]
    assert manifest["warnings"] == [
        {
            "code": "resource_unavailable",
            "instance_uuid": "missing-instance",
            "reason": "missing",
            "setting": "imageFiles[]",
            "source_name": "missing.png",
        }
    ]
    serialized_bundle = (output_path / "web_config.json").read_text(encoding="utf-8") + (
        output_path / "manifest.json"
    ).read_text(encoding="utf-8")
    assert str(missing) not in serialized_bundle


def test_export_refuses_traversal_and_outside_resources(tmp_path):
    uploads_root = tmp_path / "uploads"
    uploads_root.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"must not be copied")
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "image_upload",
                            "name": "Unsafe",
                            "plugin_settings": {
                                "imageFiles[]": [
                                    str(outside),
                                    "../outside.png",
                                ]
                            },
                            "refresh": {"interval": 600},
                            "instance_uuid": "unsafe-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, uploads_root)

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {"imageFiles[]": [None, None]}
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    assert [item["reason"] for item in manifest["resources"]] == [
        "outside_uploads_root",
        "path_traversal",
    ]
    assert [warning["reason"] for warning in manifest["warnings"]] == [
        "outside_uploads_root",
        "path_traversal",
    ]
    assert list((output_path / "resources").iterdir()) == []
    serialized_bundle = (output_path / "web_config.json").read_text(encoding="utf-8") + (
        output_path / "manifest.json"
    ).read_text(encoding="utf-8")
    assert str(outside) not in serialized_bundle
    assert "must not be copied" not in serialized_bundle


def test_export_refuses_symlink_or_junction_resource(tmp_path):
    uploads_root = tmp_path / "uploads"
    uploads_root.mkdir()
    outside_dir = tmp_path / "outside-dir"
    outside_dir.mkdir()
    outside = outside_dir / "linked.png"
    outside.write_bytes(b"must remain outside")
    link_dir = uploads_root / "linked-dir"
    if os.name == "nt":
        created = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link_dir), str(outside_dir)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert created.returncode == 0, created.stderr
    else:
        link_dir.symlink_to(outside_dir, target_is_directory=True)
    linked_resource = link_dir / "linked.png"
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "image_upload",
                            "name": "Linked",
                            "plugin_settings": {"imageFiles[]": [str(linked_resource)]},
                            "refresh": {"interval": 600},
                            "instance_uuid": "linked-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, uploads_root)

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {"imageFiles[]": [None]}
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["resources"][0]["reason"] == "symlink"
    assert manifest["warnings"][0]["reason"] == "symlink"
    assert list((output_path / "resources").iterdir()) == []
    assert "must remain outside" not in (output_path / "manifest.json").read_text(encoding="utf-8")


def test_export_includes_device_display_config_and_deterministic_plugin_catalog(
    tmp_path,
):
    config = {
        "name": "Model Y Information",
        "resolution": [800, 480],
        "orientation": "horizontal",
        "timezone": "America/Los_Angeles",
        "time_format": "24h",
        "plugin_cycle_interval_seconds": 300,
        "image_settings": {
            "saturation": 1.0,
            "brightness": 0.9,
            "sharpness": 1.1,
            "contrast": 1.0,
        },
        "plugin_order": ["image_upload", "github"],
        "playlist_config": {"playlists": [], "active_playlist": None},
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["device"] == {
        "image_settings": {
            "brightness": 0.9,
            "contrast": 1.0,
            "saturation": 1.0,
            "sharpness": 1.1,
        },
        "name": "Model Y Information",
        "orientation": "horizontal",
        "plugin_cycle_interval_seconds": 300,
        "resolution": [800, 480],
        "time_format": "24h",
        "timezone": "America/Los_Angeles",
    }
    catalog = exported["plugin_catalog"]
    assert catalog[:2] == [
        {
            "class": "ImageUpload",
            "disabled": False,
            "display_name": "Image Upload",
            "id": "image_upload",
        },
        {
            "class": "GitHub",
            "disabled": False,
            "display_name": "GitHub",
            "id": "github",
        },
    ]
    assert [plugin["id"] for plugin in catalog[2:]] == sorted(plugin["id"] for plugin in catalog[2:])


def test_export_marks_directory_and_unsupported_upload_unavailable(tmp_path):
    uploads_root = tmp_path / "uploads"
    uploads_root.mkdir()
    directory = uploads_root / "album"
    directory.mkdir()
    executable = uploads_root / "payload.exe"
    executable.write_bytes(b"not an allowed upload")
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "image_folder",
                            "name": "Unsafe resources",
                            "plugin_settings": {
                                "folder_path": str(directory),
                                "source_file": str(executable),
                            },
                            "refresh": {"interval": 600},
                            "instance_uuid": "unsupported-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, uploads_root)

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {
        "folder_path": None,
        "source_file": None,
    }
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    assert [resource["reason"] for resource in manifest["resources"]] == [
        "not_regular_file",
        "unsupported_extension",
    ]
    assert list((output_path / "resources").iterdir()) == []
    serialized_bundle = (output_path / "web_config.json").read_text(encoding="utf-8") + (
        output_path / "manifest.json"
    ).read_text(encoding="utf-8")
    assert str(directory) not in serialized_bundle
    assert str(executable) not in serialized_bundle
    assert "not an allowed upload" not in serialized_bundle


def test_export_redacts_query_and_authorization_values_under_benign_keys(tmp_path):
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "screenshot",
                            "name": "Authenticated page",
                            "plugin_settings": {
                                "endpoint": ("https://api.example.test/data?api_key=query-secret-value&city=LA"),
                                "authorization_header": "Bearer bearer-secret-value",
                            },
                            "refresh": {"interval": 600},
                            "instance_uuid": "auth-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {
        "endpoint": "https://api.example.test/data?api_key=${API_KEY}&city=LA",
        "authorization_header": "${BEARER_TOKEN}",
    }
    required = json.loads((output_path / "required_secrets.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in required["secrets"]] == [
        "API_KEY",
        "BEARER_TOKEN",
    ]
    all_text = result.stdout + result.stderr + (output_path / "web_config.json").read_text(encoding="utf-8")
    assert "query-secret-value" not in all_text
    assert "bearer-secret-value" not in all_text


def test_export_redacts_each_url_userinfo_with_a_stable_unique_placeholder(tmp_path):
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "plugins": [
                        {
                            "plugin_id": "screenshot",
                            "name": "Private endpoints",
                            "plugin_settings": {
                                "primary": "https://alice:first-password@one.example.test/feed",
                                "secondary": "https://bob:second-password@two.example.test/feed",
                            },
                            "instance_uuid": "private-endpoints",
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    first_output = tmp_path / "bundle-one"
    second_output = tmp_path / "bundle-two"

    first = run_export(config_path, first_output, tmp_path / "uploads")
    second = run_export(config_path, second_output, tmp_path / "uploads")

    assert first.returncode == 0, first.stderr
    assert second.returncode == 0, second.stderr
    first_config = json.loads((first_output / "web_config.json").read_text(encoding="utf-8"))
    second_config = json.loads((second_output / "web_config.json").read_text(encoding="utf-8"))
    first_settings = first_config["playlists"][0]["plugins"][0]["plugin_settings"]
    second_settings = second_config["playlists"][0]["plugins"][0]["plugin_settings"]
    placeholders = {value.split("${", 1)[1].split("}", 1)[0] for value in first_settings.values()}

    assert first_settings == second_settings
    assert len(placeholders) == 2
    assert all(name.startswith("URL_USERINFO_") for name in placeholders)
    assert first_settings["primary"].startswith("https://${URL_USERINFO_")
    assert first_settings["primary"].endswith("}@one.example.test/feed")
    assert first_settings["secondary"].startswith("https://${URL_USERINFO_")
    assert first_settings["secondary"].endswith("}@two.example.test/feed")
    required = json.loads((first_output / "required_secrets.json").read_text(encoding="utf-8"))
    assert {item["name"] for item in required["secrets"]} == placeholders

    public_text = "".join(
        [
            first.stdout,
            first.stderr,
            (first_output / "web_config.json").read_text(encoding="utf-8"),
            (first_output / "manifest.json").read_text(encoding="utf-8"),
        ]
    )
    for secret in ("alice", "first-password", "bob", "second-password"):
        assert secret not in public_text


def test_export_resolves_secret_env_reference_alias_to_schema_canonical(tmp_path):
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "ticketmaster_events",
                            "name": "Events",
                            "plugin_settings": {"apiKeyEnv": "TICKETMASTER_CONSUMER_KEY"},
                            "refresh": {"interval": 3600},
                            "instance_uuid": "ticketmaster-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {"apiKeyEnv": "${TICKETMASTER_API_KEY}"}
    required = json.loads((output_path / "required_secrets.json").read_text(encoding="utf-8"))
    assert required["secrets"] == [
        {
            "name": "TICKETMASTER_API_KEY",
            "source": "schema",
            "value_type": "secret",
        }
    ]


def test_export_keeps_declared_secret_path_as_placeholder_not_resource(tmp_path):
    secret_session_path = tmp_path / "uploads" / "telegram.session"
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": [
                        {
                            "plugin_id": "telegram_digest",
                            "name": "Private digest",
                            "plugin_settings": {"TELEGRAM_SESSION_PATH": str(secret_session_path)},
                            "refresh": {"interval": 3600},
                            "instance_uuid": "telegram-instance",
                            "settings_revision": 1,
                        }
                    ],
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    assert exported["playlists"][0]["plugins"][0]["plugin_settings"] == {
        "TELEGRAM_SESSION_PATH": "${TELEGRAM_SESSION_PATH}"
    }
    required = json.loads((output_path / "required_secrets.json").read_text(encoding="utf-8"))
    assert required["secrets"] == [
        {
            "name": "TELEGRAM_SESSION_PATH",
            "source": "schema",
            "value_type": "path",
        }
    ]
    manifest = json.loads((output_path / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["resources"] == []
    assert manifest["warnings"] == []
    assert str(secret_session_path) not in (output_path / "web_config.json").read_text(encoding="utf-8")


def test_export_maps_generic_inline_secret_keys_by_plugin_to_schema_canonical(
    tmp_path,
):
    plugins = []
    for plugin_id, name, value in (
        ("dota_profile_dashboard", "Dota", "dota-plain-secret"),
        ("lol_info", "League", "riot-plain-secret"),
        ("ticketmaster_events", "Events", "events-plain-secret"),
    ):
        plugins.append(
            {
                "plugin_id": plugin_id,
                "name": name,
                "plugin_settings": {"apiKey": value},
                "refresh": {"interval": 3600},
                "instance_uuid": f"{plugin_id}-instance",
                "settings_revision": 1,
            }
        )
    config = {
        "playlist_config": {
            "playlists": [
                {
                    "name": "Default",
                    "start_time": "00:00",
                    "end_time": "24:00",
                    "plugins": plugins,
                }
            ]
        }
    }
    config_path = tmp_path / "device.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    output_path = tmp_path / "bundle"

    result = run_export(config_path, output_path, tmp_path / "uploads")

    assert result.returncode == 0, result.stderr
    exported = json.loads((output_path / "web_config.json").read_text(encoding="utf-8"))
    exported_plugins = exported["playlists"][0]["plugins"]
    assert [plugin["plugin_settings"]["apiKey"] for plugin in exported_plugins] == [
        "${OPENDOTA_API_KEY}",
        "${RIOT_API_KEY}",
        "${TICKETMASTER_API_KEY}",
    ]
    required = json.loads((output_path / "required_secrets.json").read_text(encoding="utf-8"))
    assert [item["name"] for item in required["secrets"]] == [
        "OPENDOTA_API_KEY",
        "RIOT_API_KEY",
        "TICKETMASTER_API_KEY",
    ]
    all_text = result.stdout + result.stderr + (output_path / "web_config.json").read_text(encoding="utf-8")
    assert "dota-plain-secret" not in all_text
    assert "riot-plain-secret" not in all_text
    assert "events-plain-secret" not in all_text
