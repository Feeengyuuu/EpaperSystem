#!/usr/bin/env python3
"""Read-only runtime audit of the e-paper device, split into fetch, render and write.

Takes two observations of the device (default 330 s apart, one 300 s cycle plus
margin) and reports, per playlist instance, whether its data lane (fetch) and
presentation lane (render) progressed, failed or stayed idle between them, and
whether the panel kept committing (write). Never posts requests or writes device
state: it reads root-owned JSON through the approved acceptance helper's
--print-runtime-state mode and the journal over the pinned SSH identity.
"""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
HELPER = "sudo -n /opt/inkypi/current/venv_inkypi/bin/python /var/tmp/live_all_instances_acceptance.py"
CONFIG = "/var/lib/inkypi/config/device.json"
RUNTIME = "/var/lib/inkypi/data/runtime_state.json"
DISPLAY = "/var/lib/inkypi/display/display_manifest.json"
FIELD = re.compile(r"(\w+): ([^|]+?)\s*(?:\||$)")
URL = re.compile(r"https?://\S+")


def _time(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def _advanced(before, after):
    after_time = _time(after)
    before_time = _time(before)
    return after_time is not None and (before_time is None or after_time > before_time)


def lane_change(before, after):
    """Classify one runtime lane between two observations."""

    before, after = before or {}, after or {}
    succeeded = _advanced(before.get("last_success_at"), after.get("last_success_at"))
    failed = _advanced(before.get("last_failure_at"), after.get("last_failure_at"))
    if succeeded and (not failed or _time(after["last_success_at"]) >= _time(after["last_failure_at"])):
        return "succeeded"
    return "failed" if failed else "idle"


def _playlist_instances(config):
    return {
        item.get("instance_uuid"): item.get("plugin_id")
        for playlist in (config.get("playlist_config") or {}).get("playlists", [])
        for item in playlist.get("plugins", [])
        if item.get("instance_uuid")
    }


def _stage(instances, first, second, lane):
    result = {"succeeded": [], "failed": [], "idle": []}
    for uuid, plugin_id in sorted(instances.items(), key=lambda pair: pair[1] or ""):
        before = ((first.get("instances") or {}).get(uuid) or {}).get("lanes", {}).get(lane)
        after = ((second.get("instances") or {}).get(uuid) or {}).get("lanes", {}).get(lane)
        change = lane_change(before, after)
        if change == "failed":
            error = URL.sub("[url]", str((after or {}).get("last_error") or ""))[:160]
            result["failed"].append({"plugin_id": plugin_id, "error": error})
        else:
            result[change].append(plugin_id)
    return result


def stage_report(config, first, second, *, journal_writes):
    """Compare two observations; each holds captured_at, runtime state and display manifest."""

    cycle = float(config.get("plugin_cycle_interval_seconds") or 300)
    window = (_time(second["captured_at"]) - _time(first["captured_at"])).total_seconds()
    instances = _playlist_instances(config)
    display = second["display"] or {}
    committed = _time(display.get("committed_at"))
    age = None if committed is None else round((_time(second["captured_at"]) - committed).total_seconds(), 1)
    expected = max(0, math.floor(window / cycle))
    write = {
        "commits_observed": (first["display"] or {}).get("commit_id") != display.get("commit_id"),
        "journal_writes": journal_writes,
        "expected_at_least": expected,
        "hardware_written": display.get("hardware_written") is True,
        "last_commit_age_seconds": age,
    }
    if age is None or age > 2 * cycle:
        write["status"] = "stalled"
    elif journal_writes < expected or not write["hardware_written"]:
        write["status"] = "slow"
    else:
        write["status"] = "ok"
    report = {
        "window_seconds": window,
        "fetch": _stage(instances, first["runtime"], second["runtime"], "data"),
        "render": _stage(instances, first["runtime"], second["runtime"], "presentation"),
        "write": write,
    }
    if write["status"] == "stalled":
        report["verdict"] = "stalled"
    elif write["status"] != "ok" or report["fetch"]["failed"] or report["render"]["failed"]:
        report["verdict"] = "attention"
    else:
        report["verdict"] = "ok"
    return report


def journal_summary(lines):
    """Count panel writes, failed commands by plugin, and the largest command peaks."""

    writes = 0
    failures = Counter()
    peaks = {}
    for line in lines:
        if "Displaying image to Waveshare display." in line:
            writes += 1
        fields = dict(FIELD.findall(line))
        plugin_id = fields.get("plugin_id")
        if "Refresh command failed." in line and plugin_id:
            failures[plugin_id] += 1
        try:
            peak = float(fields.get("command_peak_mb", ""))
        except ValueError:
            continue
        if plugin_id:
            peaks[plugin_id] = max(peak, peaks.get(plugin_id, 0.0))
    top = sorted(peaks.items(), key=lambda pair: -pair[1])[:8]
    return {"writes": writes, "failed_commands": dict(failures.most_common()),
            "largest_command_peaks_mb": dict(top)}


class Device:
    def __init__(self, args):
        address = socket.getaddrinfo(args.host, 22, socket.AF_INET, socket.SOCK_STREAM)[0][4][0]
        self.ssh = [
            args.ssh, "-4", "-i", str(args.identity), "-o", "BatchMode=yes", "-o", "IdentitiesOnly=yes",
            "-o", "ConnectTimeout=12", "-o", f"HostKeyAlias={args.host.lower()}",
            "-o", "StrictHostKeyChecking=yes", "-o", f"UserKnownHostsFile={args.known_hosts}",
            f"{args.user}@{address}",
        ]

    def run(self, command, timeout=60):
        result = subprocess.run(self.ssh + [command], capture_output=True, timeout=timeout)
        if result.returncode:
            # Helper stderr can echo configuration; never print it.
            raise RuntimeError(f"read-only SSH command returned {result.returncode}")
        return result.stdout.decode("utf-8", "replace")

    def document(self, path):
        return json.loads(self.run(f"{HELPER} --print-runtime-state --runtime-state {shlex.quote(path)}"))

    def observe(self):
        return {"captured_at": datetime.now(timezone.utc).isoformat(),
                "release": self.run("readlink -f /opt/inkypi/current").strip(),
                "runtime": self.document(RUNTIME), "display": self.document(DISPLAY)}

    def journal_since(self, iso_time):
        epoch = int(_time(iso_time).timestamp())
        return self.run(f"journalctl -u inkypi --since @{epoch} --no-pager -o cat", timeout=120).splitlines()


def _default_ssh():
    windows_openssh = Path(r"C:\Windows\System32\OpenSSH\ssh.exe")
    return shutil.which("ssh") or (str(windows_openssh) if windows_openssh.is_file() else "ssh")


def _parser():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--ssh", default=_default_ssh())
    parser.add_argument("--host", default="ColoredEpaperFrame.local")
    parser.add_argument("--user", default="feeengyuuu")
    parser.add_argument("--identity", type=Path, default=ROOT / ".ssh" / "epaperpod_codex_20260525")
    parser.add_argument("--known-hosts", type=Path, default=ROOT / ".tmp" / "epaperpod_known_hosts")
    parser.add_argument("--interval", type=float, default=330.0, help="seconds between the two observations")
    parser.add_argument("--output", type=Path, help="write the JSON report here")
    return parser


def main(argv=None):
    args = _parser().parse_args(argv)
    device = Device(args)
    config = device.document(CONFIG)
    first = device.observe()
    time.sleep(max(0.0, args.interval))
    second = device.observe()
    journal = journal_summary(device.journal_since(first["captured_at"]))
    report = stage_report(config, first, second, journal_writes=journal["writes"])
    report.update(release=second["release"], first_release=first["release"], journal=journal)
    if first["release"] != second["release"]:
        report["verdict"] = "attention"
        report["note"] = "release changed between observations"
    text = json.dumps(report, ensure_ascii=False, indent=1)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["verdict"] == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
