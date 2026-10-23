"""Post-deployment acceptance: snapshots, health gate, journal, plugin display and probes.

Everything here is read-only on the device except ``show_plugins``, which uses
the LAN display-control endpoints enabled by ``open_display_control`` (the
owner allowed refresh/display control on 2026-10-06). Evidence is saved in the
task folder after secret-looking keys and provider URLs are removed.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import hashlib
import io
import json
import re
import shlex
import time
from urllib.parse import quote

from release_device import CONFIG, CURRENT_LINK, DISPLAY, PREVIOUS_LINK, RELEASES_DIR, RUNTIME, Device, digest, safe
from release_task import ReleaseError, TaskContext, require

JOURNAL_MARKERS = (
    "Traceback", "Exception in thread", "overran its deadline", "supervised", "restart request",
    "Runtime restart request", "task deadline expired", "HTTP retry delay exceeds task deadline",
    "quiet window", "Refresh command started.", "Waveshare display",
)
# The service logs to stdout, so journald stores application ERRORs at priority 6.
APP_ERROR = re.compile(r" - (?:ERROR|CRITICAL) - ")
LABEL = re.compile(r"[a-zA-Z0-9_-]+")
PLUGIN_ID = re.compile(r"[a-z0-9_]+")
RUNTIME_ONLY_SETTINGS = {"active_theme", "active_theme_info", "active_theme_refresh_failure",
                         "playlist_config", "refresh_info", "config_revision"}
# The scheduler rewrites the theme state on its own: active_theme flips at sunrise and
# sunset, active_theme_info carries the times, active_theme_refresh_failure records and
# clears a failed theme redraw. No settings page writes them, so a round that spans a
# theme switch may move the top-level hash; everything a user configures must not.
STABLE_CONFIGURATION = ("stable_top_level_settings_sha256", "instance_settings_sha256",
                        "instance_count", "cycle_seconds", "open_display_control")


def is_error(priority, message: str) -> bool:
    return str(priority) in {"0", "1", "2", "3"} or APP_ERROR.search(message) is not None


def parse_time(value) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def require_label(label: str) -> str:
    require(LABEL.fullmatch(label or ""), f"Unsafe evidence label: {label!r}")
    return label


def config_identity(config: dict, without_plugins=()) -> dict:
    """Hash what the owner configured, separating it from fields the runtime rewrites.

    ``without_plugins`` leaves those plugins' instances out, so a migration that
    only adds instances can be proven to leave every other instance untouched.
    """
    instances = []
    for playlist in config.get("playlist_config", {}).get("playlists", []):
        for instance in playlist.get("plugins", []):
            if instance.get("plugin_id") in without_plugins:
                continue
            instances.append({"playlist_hash": digest(playlist.get("name")), "plugin_id": instance.get("plugin_id"),
                              "instance_uuid": instance.get("instance_uuid"),
                              "settings_sha256": digest(instance.get("plugin_settings", {})),
                              "refresh": instance.get("refresh"),
                              "settings_revision": instance.get("settings_revision"),
                              "structural_generation": instance.get("structural_generation")})
    top = {key: value for key, value in config.items()
           if key not in ("playlist_config", "refresh_info", "config_revision")}
    return {"top_level_settings_without_revision_sha256": digest(top),
            "instance_settings_sha256": digest(instances), "instance_count": len(instances),
            "cycle_seconds": config.get("plugin_cycle_interval_seconds"),
            "open_display_control": config.get("open_display_control"),
            "stable_top_level_settings_sha256": digest(
                {key: value for key, value in config.items() if key not in RUNTIME_ONLY_SETTINGS})}


def configuration_note(before: dict, after: dict) -> str | None:
    """Refuse a changed user configuration; describe a runtime-only top-level change."""
    changed = [key for key in STABLE_CONFIGURATION if before.get(key) != after.get(key)]
    require(not changed, f"Live configuration changed: {changed}")
    top_level = "top_level_settings_without_revision_sha256"
    if before.get(top_level) != after.get(top_level):
        return "Only the runtime theme state changed"
    return None


REBASELINE_CHANGES = ("stable_top_level_settings_sha256", "instance_settings_sha256")


def rebaseline_configuration(ctx: TaskContext, device: Device, reason: str, migration: str,
                             added_plugins=()) -> dict:
    """Accept the deployed release's own startup migration as the acceptance configuration.

    Only settings may change (cycle and display control may not), and the
    migration's runtime marker must be on the device. The instance count may
    grow only by one new instance for each of ``added_plugins``, and every other
    instance must then be exactly as before. The original identity stays in
    configuration-rebaseline.json and preflight.json.
    """
    reason = str(reason or "").strip()
    require(reason, "A configuration rebaseline needs a reason")
    added = sorted(set(added_plugins or ()))
    require(all(PLUGIN_ID.fullmatch(plugin) for plugin in added), f"Unsafe plugin id in {added}")
    ctx.require_deployed()
    preflight = ctx.load("preflight.json")
    config = device.document(CONFIG)
    before, after = preflight["configuration"], config_identity(config)
    changed = [key for key in STABLE_CONFIGURATION if before.get(key) != after.get(key)]
    require(changed, "The live configuration did not change; nothing to rebaseline")
    accepted = REBASELINE_CHANGES + (("instance_count",) if added else ())
    refused = [key for key in changed if key not in accepted]
    require(not refused, f"A rebaseline cannot accept changes to {refused}")
    if added:
        counts = Counter(instance.get("plugin_id")
                         for playlist in config.get("playlist_config", {}).get("playlists", [])
                         for instance in playlist.get("plugins", []))
        require(all(counts[plugin] == 1 for plugin in added)
                and after["instance_count"] == before["instance_count"] + len(added),
                f"Each of {added} must add exactly one new instance")
        kept = config_identity(config, without_plugins=added)
        require(kept["instance_settings_sha256"] == before["instance_settings_sha256"],
                "Instances other than the added plugins changed")
    migrations = config.get("runtime_migrations")
    require(isinstance(migrations, dict) and migrations.get(migration) is True,
            f"Migration {migration} is not recorded on the device")
    record = {"rebaselined_at": datetime.now(timezone.utc).isoformat(), "reason": reason, "migration": migration,
              "changed": changed, "added_plugins": added, "before": before, "after": after}
    preflight["configuration"] = after
    preflight.setdefault("configuration_rebaselines", []).append(record)
    ctx.save("configuration-rebaseline.json", record)
    ctx.save("preflight.json", preflight)
    return record


def source_proof(device: Device, manifest: dict) -> dict:
    """Hash the deployed changed files on the device; removed files must be gone."""
    names = [name for name in manifest["changed_files"] if name in manifest["hashes"]]
    removed = [name for name in manifest["changed_files"] if name not in manifest["hashes"]]
    command = ("cd /opt/inkypi/current && sha256sum " + " ".join(shlex.quote(name) for name in names)
               + "; for name in " + " ".join(shlex.quote(name) for name in removed)
               + '; do [ -e "$name" ] && echo "STILL_PRESENT $name"; done; true')
    actual, still_present = {}, []
    for line in device.remote(command).decode().splitlines():
        value, name = line.split(maxsplit=1)
        if value == "STILL_PRESENT":
            still_present.append(name)
        else:
            actual[name.lstrip("*")] = value
    mismatched = [name for name in names if actual.get(name) != manifest["hashes"][name]]
    return {"checked_files": len(names), "mismatched": mismatched,
            "removed_files": len(removed), "removed_still_present": still_present}


def snapshot(ctx: TaskContext, device: Device, label: str) -> dict:
    """Save acceptance-<label>.json/.png: releases, health, display proof, source and configuration."""
    require_label(label)
    state = device.document(RUNTIME)
    manifest = device.document(DISPLAY)
    record = {"captured_at": datetime.now(timezone.utc).isoformat(),
              "active_release": device.resolved_link(CURRENT_LINK),
              "previous_release": device.resolved_link(PREVIOUS_LINK),
              "releases": device.remote("ls -1 " + RELEASES_DIR).decode().split(),
              "healthz": device.http_json("/healthz"), "readyz": device.http_json("/readyz"),
              "runtime_updated_at": state.get("updated_at"), "display": safe(manifest),
              "runtime_display": safe(state.get("display")),
              "source": source_proof(device, ctx.load("current-manifest.json")),
              "configuration": config_identity(device.document(CONFIG))}
    service = device.remote("systemctl show inkypi --property=MainPID --property=NRestarts --property=ActiveState "
                            "--property=SubState --property=ActiveEnterTimestamp").decode()
    record["service"] = dict(line.split("=", 1) for line in service.splitlines() if "=" in line)
    record["var_tmp_transport_zips"] = device.remote(
        "ls -1 /var/tmp | grep -c '^inkypi-.*\\.zip$' || true").decode().strip()
    response = device.client.get(device.base_url + "/api/current_image", timeout=(3, 20))
    image = {"http_status": response.status_code, "etag": response.headers.get("ETag", "").strip('"')}
    if response.status_code == 200:
        from PIL import Image

        with Image.open(io.BytesIO(response.content)) as picture:
            picture.load()
            image.update(dimensions=list(picture.size),
                         pixel_hash=hashlib.sha256(picture.convert("RGB").tobytes()).hexdigest())
        image["matches_manifest"] = (image["etag"] == manifest.get("commit_id")
                                     and image["pixel_hash"] == manifest.get("pixel_hash"))
        image["matches_runtime_commit"] = image["etag"] == (state.get("display") or {}).get("commit_id")
        ctx.path("acceptance-" + label + ".png").write_bytes(response.content)
    record["image"] = image
    ctx.save("acceptance-" + label + ".json", record)
    return record


def acceptance_snapshot(ctx: TaskContext, device: Device, label: str) -> dict:
    """Snapshot, then require preserved configuration and the exact deployed source."""
    record = snapshot(ctx, device, label)
    preflight = ctx.load("preflight.json")
    note = configuration_note(preflight["configuration"], record["configuration"])
    if note:
        record["configuration"]["top_level_note"] = note
    require(not record["source"]["mismatched"] and not record["source"]["removed_still_present"],
            f"Deployed source differs from the manifest: {record['source']}")
    record["configuration_preserved"] = True
    ctx.save("acceptance-" + label + ".json", record)
    return record


def journal(ctx: TaskContext, device: Device, since: str) -> tuple[list[dict], dict]:
    """Save acceptance-journal.json/.log with markers and errors since ``since``."""
    epoch = int(parse_time(since).timestamp())
    command = shlex.join(["journalctl", "-u", "inkypi", "--since", "@" + str(epoch), "--no-pager", "-o", "json"])
    selected, priorities, started = [], Counter(), Counter()
    for raw in device.remote(command, timeout=90).decode("utf-8", "replace").splitlines():
        entry = json.loads(raw)
        message = entry.get("MESSAGE", "")
        if not isinstance(message, str):
            continue
        priorities[str(entry.get("PRIORITY", "?"))] += 1
        if "Refresh command started." in message:
            fields = dict(re.findall(r"(source|intent|plugin_id): ([a-z0-9_]+)", message))
            started[(fields.get("source"), fields.get("intent"))] += 1
        if any(marker in message for marker in JOURNAL_MARKERS) or is_error(entry.get("PRIORITY"), message):
            selected.append({"timestamp": datetime.fromtimestamp(int(entry["__REALTIME_TIMESTAMP"]) / 1e6,
                                                                 timezone.utc).isoformat(),
                             "priority": entry.get("PRIORITY"), "message": safe(message)[:600]})
    summary = summarize_journal(since, selected, priorities, started)
    ctx.save("acceptance-journal.json", {"summary": summary, "selected": selected})
    ctx.path("acceptance-journal.log").write_text(
        "\n".join(item["timestamp"] + " p" + str(item["priority"]) + " " + item["message"] for item in selected) + "\n",
        encoding="utf-8")
    return selected, summary


def summarize_journal(since: str, selected: list[dict], priorities: Counter, started: Counter) -> dict:
    errors = [item for item in selected if is_error(item["priority"], item["message"])]

    def plugin_of(message: str) -> str:
        match = re.search(r"plugin_id: ([a-z0-9_]+)", message) or re.search(r" - ERROR - ([a-z_.]+)", message)
        return match.group(1) if match else "unknown"

    return {"since": since, "priority_counts": dict(priorities),
            "commands_started": {f"{source}/{intent}": count for (source, intent), count in started.items()},
            "marker_counts": {marker: sum(marker in item["message"] for item in selected)
                              for marker in JOURNAL_MARKERS},
            "error_lines": errors[-40:],
            "app_errors_by_plugin": dict(Counter(plugin_of(item["message"]) for item in errors))}


def journal_check(ctx: TaskContext, device: Device) -> dict:
    deployment = ctx.require_deployed()
    _entries, summary = journal(ctx, device, deployment["completed_at"])
    return {"error_lines": summary["error_lines"], "display_writes": summary["marker_counts"].get("Waveshare display"),
            "commands_started": summary["commands_started"]}


def display_origin(manifest: dict, entries: list[dict]) -> dict | None:
    """Return the refresh command that produced ``manifest``'s commit, if the journal shows it."""
    committed = parse_time(manifest["committed_at"])
    eligible = [entry for entry in entries
                if "Refresh command started." in entry["message"] and parse_time(entry["timestamp"]) <= committed]
    if not eligible:
        return None
    latest = eligible[-1]
    fields = dict(re.findall(r"(source|intent|plugin_id): ([a-z0-9_]+)", latest["message"]))
    fields["started_at"] = latest["timestamp"]
    if fields.get("plugin_id") != manifest.get("logical_target", {}).get("plugin_id"):
        return None
    return fields


def watch(ctx: TaskContext, device: Device, timeout: float, interval: float, count: int) -> dict:
    """Wait for ``count`` natural scheduler hardware commits on the deployed release."""
    deployment = ctx.require_deployed()
    expected, since = deployment["release_id"], deployment["started_at"]
    earliest = parse_time(deployment["completed_at"])
    begin = time.monotonic()
    observed, classified = {}, set()
    while time.monotonic() - begin < timeout:
        record = snapshot(ctx, device, "latest")
        require(record["active_release"].endswith("/" + expected), "Unexpected release during acceptance")
        manifest = record["display"]
        commit_id = manifest.get("commit_id")
        if (parse_time(manifest["committed_at"]) >= earliest and manifest.get("hardware_written") is True
                and record["image"].get("matches_manifest") and record["image"].get("matches_runtime_commit")
                and commit_id not in classified):
            classified.add(commit_id)
            entries, _summary = journal(ctx, device, since)
            origin = display_origin(manifest, entries)
            counted = origin is not None and origin.get("source") == "scheduler"
            if counted:
                record["origin_command"] = origin
                observed[commit_id] = record
                ctx.save(f"acceptance-commit-{len(observed)}.json", record)
                ctx.path(f"acceptance-commit-{len(observed)}.png").write_bytes(ctx.path("acceptance-latest.png").read_bytes())
            print(json.dumps({"event": "commit", "counted": counted, "count": len(observed),
                              "committed_at": manifest["committed_at"],
                              "plugin_id": manifest.get("logical_target", {}).get("plugin_id"), "origin": origin}),
                  flush=True)
        if len(observed) >= count:
            final = snapshot(ctx, device, "final")
            _entries, summary = journal(ctx, device, since)
            report = {"status": "natural_hardware_commits_observed", "release": expected, "since": since,
                      "commits": [{"commit_id": key, "committed_at": value["display"]["committed_at"],
                                   "plugin_id": value["display"].get("logical_target", {}).get("plugin_id"),
                                   "origin": value.get("origin_command")} for key, value in observed.items()],
                      "final_readyz": final["readyz"], "final_service": final["service"],
                      "source": final["source"], "journal": summary,
                      "completed_at": datetime.now(timezone.utc).isoformat()}
            ctx.save("acceptance-monitor.json", report)
            return {"status": report["status"], "count": len(observed)}
        time.sleep(max(5, min(interval, 60)))
    journal(ctx, device, since)
    raise ReleaseError("Observation window ended before the requested natural commit count")


def classify_instances(config: dict, runtime: dict, now: datetime) -> tuple[dict[str, str], dict[str, float]]:
    """Instances whose DATA lane failed after its last success, and those whose success is stale.

    "Stalled" means no DATA success for max(900 s, 2 x interval); it is
    informational because /readyz applies the device's exact due rules.
    """
    failing, stalled = {}, {}
    for playlist in config["playlist_config"]["playlists"]:
        for item in playlist.get("plugins", []):
            instance = (runtime.get("instances") or {}).get(item["instance_uuid"]) or {}
            lane = (instance.get("lanes") or {}).get("data") or {}
            success, failure = parse_time(lane.get("last_success_at")), parse_time(lane.get("last_failure_at"))
            name = item["plugin_id"]
            if failure and (success is None or failure > success):
                failing[name] = safe(str(lane.get("last_error") or ""))[:160]
            interval = (item.get("refresh") or {}).get("interval")
            if isinstance(interval, (int, float)) and interval > 0 and success is not None:
                age = (now - success).total_seconds()
                if age >= max(900.0, 2.0 * interval):
                    stalled[name] = round(age / 3600, 2)
    return failing, stalled


def record_health(ctx: TaskContext, device: Device, label: str) -> dict:
    """Save health-<label>.json from the device's own runtime state (never posts)."""
    require_label(label)
    now = datetime.now(timezone.utc)
    config = device.document(CONFIG)
    readyz = device.http_json("/readyz")
    failing, stalled = classify_instances(config, device.document(RUNTIME), now)
    document = {"label": label, "captured_at": now.isoformat(), "release": device.resolved_link(CURRENT_LINK),
                "readyz": readyz.get("payload", {}).get("status"), "readyz_http": readyz.get("http_status"),
                "failing": failing, "stalled_hours": stalled}
    ctx.save(f"health-{label}.json", document)
    return document


def compare_health(before: dict, after: dict, require_ready: bool) -> dict:
    new_failing = sorted(set(after["failing"]) - set(before["failing"]))
    return {"before": before["label"], "after": after["label"], "readyz": [before["readyz"], after["readyz"]],
            "new_failing": new_failing,
            "new_stalled": sorted(set(after["stalled_hours"]) - set(before["stalled_hours"])),
            "cleared_failing": sorted(set(before["failing"]) - set(after["failing"])),
            "still_failing": sorted(after["failing"]),
            "passed": not new_failing and (after["readyz"] == "ready" or not require_ready)}


def health_gate(ctx: TaskContext, before_label: str, after_label: str, require_ready: bool) -> dict:
    verdict = compare_health(ctx.load(f"health-{require_label(before_label)}.json"),
                             ctx.load(f"health-{require_label(after_label)}.json"), require_ready)
    ctx.save(f"health-gate-{after_label}.json", verdict)
    return verdict


def find_instance(config: dict, plugin: str) -> tuple[str, dict]:
    matches = [(playlist.get("name"), item) for playlist in config["playlist_config"]["playlists"]
               for item in playlist.get("plugins", []) if item.get("plugin_id") == plugin]
    require(len(matches) == 1, f"expected one {plugin} instance, found {len(matches)}")
    return matches[0]


def show_plugins(ctx: TaskContext, device: Device, plugins: list[str], log=print) -> dict:
    """Refresh and display each plugin once; save acceptance-<plugin>-shown.json per hardware write."""
    for plugin in plugins:
        require(PLUGIN_ID.fullmatch(plugin), f"Unsafe plugin id: {plugin!r}")
    deployment = ctx.require_deployed()
    active = device.resolved_link(CURRENT_LINK)
    require(active.endswith("/" + deployment["release_id"]), f"Unexpected active release: {active}")
    config = device.document(CONFIG)
    require(config.get("open_display_control") is True, "Display control is not open on this device")
    page = device.client.get(device.base_url + "/", timeout=(3, 15))
    token = re.search(r'<meta name="inkypi-csrf-token" content="([^"]+)"', page.text)
    require(token, "CSRF token not offered by the device page")
    headers = {"X-CSRF-Token": token.group(1)}
    started = datetime.now(timezone.utc)
    results = {}

    def emit(**fields):
        log(json.dumps(fields, ensure_ascii=False), flush=True)

    for plugin in plugins:
        playlist_name, instance = find_instance(config, plugin)
        payload = {"playlist_name": playlist_name, "plugin_id": plugin, "plugin_instance": instance.get("name")}

        def post(route, plugin=plugin, payload=payload):
            response = device.client.post(device.base_url + route, json=payload, headers=headers, timeout=(3, 30))
            body = response.json() if "json" in response.headers.get("Content-Type", "") else {}
            emit(plugin=plugin, step=route, http_status=response.status_code, success=body.get("success"))
            require(response.status_code in (200, 202), f"{route} returned {response.status_code}")

        requested = datetime.now(timezone.utc)
        post("/refresh_plugin_instance")
        deadline, outcome = time.monotonic() + 420, "timeout"
        while time.monotonic() < deadline:
            time.sleep(10)
            lane = device.document(RUNTIME)["instances"][instance["instance_uuid"]]["lanes"]["data"]
            success, failure = parse_time(lane.get("last_success_at")), parse_time(lane.get("last_failure_at"))
            if success and success > requested:
                outcome = "succeeded"
                break
            if failure and failure > requested:
                outcome = "failed: " + safe(str(lane.get("last_error")))[:200]
                break
        emit(plugin=plugin, step="data_refresh", outcome=outcome)
        record = {"data_refresh": outcome}
        if outcome == "succeeded":
            shown_after = datetime.now(timezone.utc)
            post("/display_plugin_instance")
            deadline = time.monotonic() + 300
            while time.monotonic() < deadline:
                time.sleep(10)
                manifest = device.document(DISPLAY)
                if (manifest.get("logical_target", {}).get("plugin_id") == plugin
                        and parse_time(manifest["committed_at"]) > shown_after
                        and manifest.get("hardware_written") is True):
                    shown = acceptance_snapshot(ctx, device, plugin + "-shown")
                    record.update(display="written", committed_at=manifest["committed_at"],
                                  matches_manifest=shown["image"].get("matches_manifest"))
                    break
            else:
                record["display"] = "not committed within 5 minutes"
            emit(plugin=plugin, step="display",
                 **{key: value for key, value in record.items() if key != "data_refresh"})
            time.sleep(20)
        results[plugin] = record

    pattern = "|".join(re.escape(plugin) for plugin in plugins)
    lines = device.remote(shlex.join(["journalctl", "-u", "inkypi", "--since", "@" + str(int(started.timestamp())),
                                      "--no-pager", "-o", "cat"])
                          + f" | grep -E '({pattern})' | grep -E ' - (ERROR|WARNING) - |Error:' || true",
                          timeout=90).decode("utf-8", "replace").splitlines()
    summary = {"checked_at": datetime.now(timezone.utc).isoformat(), "release": deployment["release_id"],
               "plugins": results, "journal_problems": [safe(line)[:300] for line in lines]}
    ctx.save("plugins-acceptance.json", summary)
    require(all(record.get("display") == "written" for record in results.values()),
            "Not every plugin reached a hardware write; see plugins-acceptance.json")
    return summary


def render_file_name(label: str, plugin: str, instance: str) -> str:
    """A stable per-instance file name (``hash()`` of a string changes every process)."""
    return f"{label}-{plugin}-{hashlib.sha256(instance.encode('utf-8')).hexdigest()[:8]}.png"


def probe_renders(ctx: TaskContext, device: Device, label: str, plugins: list[str]) -> list[dict]:
    """Save the latest device render of every instance of ``plugins`` (never posts)."""
    require_label(label)
    wanted = set(plugins)
    found = []
    for playlist in device.document(CONFIG)["playlist_config"]["playlists"]:
        for item in playlist.get("plugins", []):
            plugin = item.get("plugin_id")
            if plugin not in wanted:
                continue
            route = "/plugin_instance_image/{}/{}/{}".format(
                quote(playlist.get("name"), safe=""), quote(plugin, safe=""), quote(item.get("name"), safe=""))
            response = device.client.get(device.base_url + route, timeout=(3, 30))
            record = {"playlist": playlist.get("name"), "plugin_id": plugin, "instance": item.get("name"),
                      "http": response.status_code}
            if response.status_code == 200:
                name = render_file_name(label, plugin, item.get("name") or "")
                ctx.path(name).write_bytes(response.content)
                record["image_file"] = name
            found.append(record)
    return found
