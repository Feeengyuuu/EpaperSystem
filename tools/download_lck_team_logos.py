#!/usr/bin/env python3
"""Sync LCK team logos from the official LoL Esports schedule."""

from __future__ import annotations

import json
import re
import sys
import urllib.request
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

from PIL import Image


API_KEY = "0TvQnueqKa5mxJntVWt0w4LpLfEkrV1Ta8rQBb9Z"
LCK_LEAGUE_ID = "98767991310872058"
SCHEDULE_URL = (
    "https://esports-api.lolesports.com/persisted/gw/getSchedule"
    f"?hl=en-US&leagueId={LCK_LEAGUE_ID}"
)
TARGET_DIR = (
    Path(__file__).resolve().parents[1]
    / "inkypi-weather"
    / "package"
    / "InkyPi"
    / "src"
    / "plugins"
    / "sports_dashboard"
    / "assets"
    / "logos"
    / "lck"
)


def _request(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json,image/*,*/*",
            "User-Agent": "InkyPi-LCK-Logo-Sync/1.0",
            "x-api-key": API_KEY,
        },
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        return response.read()


def _slug(value: str) -> str:
    return re.sub(r"[^a-z0-9_-]+", "", str(value or "").strip().lower())


def _extract_teams(payload: dict) -> dict[str, dict[str, str]]:
    teams: dict[str, dict[str, str]] = {}
    for event in payload.get("data", {}).get("schedule", {}).get("events", []) or []:
        for team in ((event.get("match") or {}).get("teams") or []):
            code = str(team.get("code") or "").strip().upper()
            image = str(team.get("image") or "").strip()
            if not code or code.startswith("TBD") or not image:
                continue
            teams[code] = {
                "code": code,
                "name": str(team.get("name") or code).strip(),
                "source": image,
            }
    return dict(sorted(teams.items()))


def _save_logo(team: dict[str, str]) -> Path:
    code = _slug(team["code"])
    if not code:
        raise ValueError(f"bad team code: {team!r}")
    target = TARGET_DIR / f"{code}.png"
    data = _request(team["source"])
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGBA")
        bbox = image.getbbox()
        if bbox:
            image = image.crop(bbox)
        image.save(target, format="PNG", optimize=True)
    return target


def main() -> int:
    TARGET_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.loads(_request(SCHEDULE_URL).decode("utf-8"))
    teams = _extract_teams(payload)
    if not teams:
        print("No LCK teams found in official schedule.", file=sys.stderr)
        return 1

    manifest = {
        "league": "LCK",
        "league_id": LCK_LEAGUE_ID,
        "source": SCHEDULE_URL,
        "synced_at": datetime.now(timezone.utc).isoformat(),
        "teams": [],
    }
    for team in teams.values():
        path = _save_logo(team)
        manifest["teams"].append({**team, "file": path.name})
        print(f"{team['code']:>4} {team['name']} -> {path}")

    (TARGET_DIR / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(f"Synced {len(manifest['teams'])} LCK team logos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())