from __future__ import annotations

import argparse
import base64
import json
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path


DEFAULT_LOCKFILES = [
    Path(r"C:\Riot Games\League of Legends\lockfile"),
    Path(r"C:\Program Files\Riot Games\League of Legends\lockfile"),
    Path.home() / r"AppData\Local\Riot Games\League of Legends\lockfile",
]

DEFAULT_OUTPUT = (
    Path("inkypi-weather")
    / "package"
    / "InkyPi"
    / "src"
    / "plugins"
    / "lol_info"
    / ".lol_info_cache"
    / "league_client_matches.json"
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export League Client match history for the LoLInfo plugin."
    )
    parser.add_argument("--lockfile", type=Path, help="Path to the League Client lockfile.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUTPUT, help="Output JSON file.")
    parser.add_argument("--count", type=int, default=20, help="Number of recent games to request.")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output.")
    return parser.parse_args()


def find_lockfile(explicit: Path | None) -> Path:
    if explicit:
        if explicit.exists():
            return explicit
        raise SystemExit(f"League Client lockfile not found: {explicit}")
    for path in DEFAULT_LOCKFILES:
        if path.exists():
            return path
    process_lockfile = lockfile_from_league_process()
    if process_lockfile and process_lockfile.exists():
        return process_lockfile
    searched = "\n".join(str(path) for path in DEFAULT_LOCKFILES)
    raise SystemExit(
        "League Client lockfile not found. Start League of Legends first.\n"
        f"Searched:\n{searched}\nLeagueClientUx process path was also checked."
    )


def lockfile_from_league_process() -> Path | None:
    if not sys.platform.startswith("win"):
        return None
    command = [
        r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe",
        "-NoProfile",
        "-Command",
        "(Get-Process LeagueClientUx -ErrorAction SilentlyContinue | Select-Object -First 1 -ExpandProperty Path)",
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    except Exception:
        return None
    exe_path = (result.stdout or "").strip().splitlines()
    if not exe_path:
        return None
    return Path(exe_path[0]).parent / "lockfile"


def read_lockfile(path: Path) -> dict[str, str]:
    parts = path.read_text(encoding="utf-8").strip().split(":")
    if len(parts) != 5:
        raise SystemExit(f"Unexpected lockfile format: {path}")
    name, pid, port, password, protocol = parts
    return {
        "name": name,
        "pid": pid,
        "port": port,
        "password": password,
        "protocol": protocol,
    }


def lcu_get(lock: dict[str, str], path: str, params: dict[str, str | int] | None = None):
    query = urllib.parse.urlencode(params or {})
    url = f"{lock['protocol']}://127.0.0.1:{lock['port']}{path}"
    if query:
        url = f"{url}?{query}"
    token = base64.b64encode(f"riot:{lock['password']}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, headers={"Authorization": f"Basic {token}"})
    context = ssl._create_unverified_context()
    try:
        with urllib.request.urlopen(request, context=context, timeout=15) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"LCU request failed {exc.code}: {path}\n{detail}") from exc
    except urllib.error.URLError as exc:
        raise SystemExit(f"LCU request failed: {path}\n{exc}") from exc


def main() -> int:
    args = parse_args()
    lockfile = find_lockfile(args.lockfile)
    lock = read_lockfile(lockfile)

    summoner = lcu_get(lock, "/lol-summoner/v1/current-summoner")
    puuid = str(summoner.get("puuid") or "").strip()
    if not puuid:
        raise SystemExit("LCU current summoner did not include a PUUID.")

    count = max(1, min(100, int(args.count or 20)))
    history = lcu_get(
        lock,
        f"/lol-match-history/v1/products/lol/{urllib.parse.quote(puuid, safe='')}/matches",
        {"begIndex": 0, "endIndex": count},
    )
    payload = {
        "source": "league-client-api",
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "puuid": puuid,
        "summoner": summoner,
        "games": history,
    }

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2 if args.pretty else None),
        encoding="utf-8",
    )
    print(f"Exported {count} requested League Client games to {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
