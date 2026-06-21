#!/usr/bin/env python3
"""Download CS2 team logos for the sports_dashboard Valve sidebar.

Team IDs/names come from CSAPI rankings. Logo files come from the public
lootmarket/esport-team-logos GitHub repository using explicit path aliases, not
fuzzy page matching.
"""

from __future__ import annotations

import argparse
import json
import time
import unicodedata
import urllib.request
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

try:
    from PIL import Image
except ModuleNotFoundError:  # pragma: no cover - local runtime should have PIL.
    Image = None

CSAPI_BASE_URL = "https://api.csapi.de"
LOOTMARKET_RAW_BASE_URL = "https://raw.githubusercontent.com/lootmarket/esport-team-logos/master"
LOOTMARKET_REPO_URL = "https://github.com/lootmarket/esport-team-logos"
USER_AGENT = "EpaperSystem/1.0 (CS2 team logo cache; local project)"
DEFAULT_LIMIT = 30
REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = REPO_ROOT / "inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/assets/logos/cs2"
MANIFEST_NAME = "manifest.json"

# CSAPI team IDs are stable enough for exact mapping. Keep this table explicit:
# it is safer than fuzzy file/page search because many esports team names have
# academies, old rosters, or unrelated historical pages.
LOGO_PATH_BY_CSAPI_ID = {
    7020: "csgo/team-spirit/team-spirit-logo.png",
    11283: "csgo/falcons/falcons-logo.png",
    9565: "csgo/team-vitality/team-vitality-logo.png",
    4608: "csgo/natus-vincere/natus-vincere-logo.png",
    8297: "csgo/furia/furia-logo.png",
    4494: "csgo/mouz/mouz-logo.png",
    12468: "csgo/legacy/legacy-logo.png",
    11861: "csgo/aurora/aurora-logo.png",
    5995: "csgo/g2-esports/g2-esports-logo.png",
    # FUT is missing from the repo's cs/csgo directories; use the same org logo
    # from its Valorant folder rather than incorrectly matching another CS team.
    13286: "valorant/fut/fut-logo.png",
    12394: "csgo/betboom/betboom-logo.png",
    9996: "csgo/9z/9z-logo.png",
}

LOGO_PATH_BY_NORMALIZED_NAME = {
    "spirit": LOGO_PATH_BY_CSAPI_ID[7020],
    "falcons": LOGO_PATH_BY_CSAPI_ID[11283],
    "vitality": LOGO_PATH_BY_CSAPI_ID[9565],
    "natusvincere": LOGO_PATH_BY_CSAPI_ID[4608],
    "navi": LOGO_PATH_BY_CSAPI_ID[4608],
    "furia": LOGO_PATH_BY_CSAPI_ID[8297],
    "mouz": LOGO_PATH_BY_CSAPI_ID[4494],
    "legacy": LOGO_PATH_BY_CSAPI_ID[12468],
    "aurora": LOGO_PATH_BY_CSAPI_ID[11861],
    "g2": LOGO_PATH_BY_CSAPI_ID[5995],
    "g2esports": LOGO_PATH_BY_CSAPI_ID[5995],
    "fut": LOGO_PATH_BY_CSAPI_ID[13286],
    "futesports": LOGO_PATH_BY_CSAPI_ID[13286],
    "betboom": LOGO_PATH_BY_CSAPI_ID[12394],
    "betboomteam": LOGO_PATH_BY_CSAPI_ID[12394],
    "9z": LOGO_PATH_BY_CSAPI_ID[9996],
    "9zteam": LOGO_PATH_BY_CSAPI_ID[9996],
}


@dataclass
class Team:
    id: int
    name: str
    rank: int | None = None


def request_json(url: str, headers: dict[str, str] | None = None, timeout: int = 30) -> Any:
    req = urllib.request.Request(url, headers=headers or {"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        data = response.read()
    return json.loads(data.decode("utf-8", "replace"))


def request_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def fetch_rankings(limit: int, csapi_base_url: str) -> list[Team]:
    url = csapi_base_url.rstrip("/") + "/rankings"
    payload = request_json(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    rankings = payload.get("rankings") if isinstance(payload, dict) else payload
    teams: list[Team] = []
    for item in rankings or []:
        if not isinstance(item, dict):
            continue
        try:
            team_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        rank = item.get("rank")
        try:
            rank = int(rank) if rank is not None else None
        except (TypeError, ValueError):
            rank = None
        teams.append(Team(team_id, name, rank))
        if len(teams) >= limit:
            break
    return teams


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return "".join(ch for ch in normalized.lower() if ch.isalnum())


def logo_path_for_team(team: Team) -> str | None:
    return LOGO_PATH_BY_CSAPI_ID.get(team.id) or LOGO_PATH_BY_NORMALIZED_NAME.get(slugify(team.name))


def raw_url_for_logo(path: str) -> str:
    return LOOTMARKET_RAW_BASE_URL.rstrip("/") + "/" + path.lstrip("/")


def write_logo(data: bytes, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if Image is None:
        path.write_bytes(data)
        return
    with Image.open(BytesIO(data)) as source:
        image = source.convert("RGBA")
        bbox = image.getbbox()
        if bbox:
            image = image.crop(bbox)
        image.thumbnail((512, 512), Image.Resampling.LANCZOS)
        image.save(path, format="PNG", optimize=True)


def load_manifest(output_dir: Path) -> dict[str, Any]:
    path = output_dir / MANIFEST_NAME
    if not path.exists():
        return {"source": LOOTMARKET_REPO_URL, "teams": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"source": LOOTMARKET_REPO_URL, "teams": {}}


def clean_output_dir(output_dir: Path) -> None:
    if not output_dir.exists():
        return
    for path in output_dir.iterdir():
        if path.is_file() and path.suffix.lower() in {".png", ".webp", ".jpg", ".jpeg", ".json"}:
            path.unlink()


def save_manifest(output_dir: Path, manifest: dict[str, Any]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest["source"] = LOOTMARKET_REPO_URL
    manifest["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    manifest["license_note"] = "Downloaded from lootmarket/esport-team-logos; team logos may be trademarks of their owners."
    (output_dir / MANIFEST_NAME).write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="Download CS2 team logos into sports_dashboard assets.")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Number of CSAPI ranked teams to process.")
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR, help="Directory for normalized PNG logos.")
    parser.add_argument("--csapi-base-url", default=CSAPI_BASE_URL)
    parser.add_argument("--force", action="store_true", help="Redownload logos that already exist.")
    parser.add_argument("--clean", action="store_true", help="Remove existing logo files in the output directory first.")
    args = parser.parse_args()

    output_dir = args.output_dir.resolve()
    if args.clean:
        clean_output_dir(output_dir)
    teams = fetch_rankings(max(1, args.limit), args.csapi_base_url)
    manifest = load_manifest(output_dir)
    manifest.setdefault("teams", {})
    downloaded = 0
    skipped = 0
    failed = 0
    missing = 0

    print(f"Processing {len(teams)} CS2 teams into {output_dir}")
    for team in teams:
        logo_path = logo_path_for_team(team)
        if not logo_path:
            missing += 1
            print(f"MISS {team.rank or '-'} {team.name} ({team.id}) no explicit lootmarket path")
            continue
        target = output_dir / f"{team.id}.png"
        if target.exists() and not args.force:
            skipped += 1
            print(f"SKIP {team.rank or '-'} {team.name} ({team.id}) already exists")
            continue
        try:
            image_url = raw_url_for_logo(logo_path)
            image_data = request_bytes(image_url)
            write_logo(image_data, target)
            slug = slugify(team.name)
            if slug:
                slug_path = output_dir / f"{slug}.png"
                slug_path.write_bytes(target.read_bytes())
            manifest["teams"][str(team.id)] = {
                "name": team.name,
                "rank": team.rank,
                "file": target.name,
                "slug_file": f"{slug}.png" if slug else "",
                "source_repo": LOOTMARKET_REPO_URL,
                "source_path": logo_path,
                "source_url": image_url,
            }
            downloaded += 1
            print(f"OK   {team.rank or '-'} {team.name} ({team.id}) <- {logo_path}")
        except Exception as exc:  # pragma: no cover - network/tooling failures are reported to CLI.
            failed += 1
            print(f"FAIL {team.rank or '-'} {team.name} ({team.id}) {type(exc).__name__}: {exc}")
    save_manifest(output_dir, manifest)
    print(f"done: downloaded={downloaded} skipped={skipped} missing={missing} failed={failed} manifest={output_dir / MANIFEST_NAME}")
    return 0 if failed == 0 and (downloaded > 0 or skipped > 0 or missing >= 0) else 1


if __name__ == "__main__":
    raise SystemExit(main())