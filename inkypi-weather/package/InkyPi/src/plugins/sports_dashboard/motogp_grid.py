"""The published Grand Prix starting grid, separate from qualifying classifications.

The website's event-level JSON has no race/sprint discriminator (and its ``penalty``
may be null even after penalties). The current event's explicitly associated RAC
official document supplies every position and bike number; JSON only enriches riders.
No Q1/Q2 merge, penalty arithmetic or guessed Sprint/GP sharing is involved. An
unpublished, ambiguous or partially parsed document means no grid, not a shorter one.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from datetime import datetime, timezone
from urllib.parse import urlsplit

from . import extra_feeds

API = "https://api.motogp.pulselive.com/motogp/v1/sportsdata-gateway/events/pulse/grid"
VERSION = "motogp-grid-v1"
TTL_SECONDS = 5 * 60
JSON_MAX_BYTES = 512 * 1024
PDF_MAX_BYTES = 1024 * 1024
MAX_ROWS = 40
_ROW = re.compile(
    r"(?m)^[ \t]*(\d{1,2})[ \t]*\n"
    r"(?:\d{1,2}[':]\d{2}\.\d{3}[ \t]*\n)?"
    r"(\d{1,3})[ \t]+([^\n]+)\n([A-Za-z][A-Za-z -]*)[ \t]*$"
)
_RIDER_LINE = re.compile(r"(?m)^(\d{1,3})[ \t]+([^\W\d_][^\n]+)$")


def _positive(value, maximum):
    return type(value) is int and 0 < value <= maximum


def _unique_rows(rows):
    return (isinstance(rows, list) and 0 < len(rows) <= MAX_ROWS
            and all(isinstance(row, dict) and _positive(row.get("pos"), MAX_ROWS)
                    and _positive(row.get("number"), 999) for row in rows)
            and len({row["pos"] for row in rows}) == len(rows)
            and len({row["number"] for row in rows}) == len(rows))


def target(data):
    """One explicit race/document identity, or None. Never infer it from a Q2 file.

    A future season may qualify Sprint and GP separately. Requiring the RAC session
    and RAC document in every season keeps that association explicit.
    """
    weekend = data.get("current") or data.get("next") or {}
    season, short = data.get("season"), str(weekend.get("short") or "")
    if not _positive(season, 9999) or not re.fullmatch(r"[A-Z0-9]{2,4}", short) or not weekend.get("id"):
        return None
    races = [session for session in data.get("sessions") or []
             if isinstance(session, dict) and session.get("type") == "RAC" and session.get("id")]
    if len(races) != 1:
        return None
    session = races[0]
    url = str(session.get("grid_url") or "")
    try:
        parsed = urlsplit(url)
    except ValueError:
        return None
    expected = f"/files/results/{season}/{short}/MotoGP/RAC/Grid.pdf"
    if (parsed.scheme != "https" or parsed.netloc != "resources.motogp.com"
            or parsed.path != expected or parsed.query or parsed.fragment):
        return None
    return {"event_id": str(weekend["id"]), "season": season, "short": short,
            "session_id": str(session["id"]), "target_type": "RAC", "source_url": url}


def normalize_json(payload):
    """All official website grid rows, with their supplied positions unchanged."""
    if not isinstance(payload, dict) or not isinstance(payload.get("grid"), list):
        return []
    rows = []
    for raw in payload["grid"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("rider"), dict):
            return []
        rider = raw["rider"]
        career = rider.get("career") if isinstance(rider.get("career"), dict) else {}
        country = rider.get("country") if isinstance(rider.get("country"), dict) else {}
        rows.append({"pos": raw.get("position"), "number": career.get("number"),
                     "name": " ".join(str(rider.get(key) or "").strip() for key in ("name", "surname")).strip()[:64],
                     "uuid": str(rider.get("id") or "")[:36], "iso": str(country.get("iso") or "")[:2],
                     "team": str(raw.get("team_name") or "").strip()[:80], "constructor": ""})
    uuids = [row["uuid"] for row in rows if row["uuid"]]
    if not _unique_rows(rows) or len(uuids) != len(set(uuids)) or any(not row["name"] for row in rows):
        return []
    return sorted(rows, key=lambda row: row["pos"])


def parse_document(content):
    """Read the bounded one-page official grid; reject changed/incomplete layouts."""
    if not isinstance(content, bytes) or not content.startswith(b"%PDF-") or len(content) > PDF_MAX_BYTES:
        return []
    import fitz

    try:
        with fitz.open(stream=content, filetype="pdf") as document:
            if len(document) != 1:
                return []
            text = document[0].get_text().replace("\r", "")
    except (RuntimeError, ValueError):
        return []
    if ("OFFICIAL STARTING GRID" not in text.splitlines() or "PROVISIONAL STARTING GRID" in text
            or "TISSOT SPRINT" in text or "Official MotoGP Timing" not in text
            or not re.search(r"(?m)^RACE\s*:", text)):
        return []
    start = re.search(r"(?m)^[ \t]*\d{1,2}[ \t]*$", text)
    end = re.search(r"(?m)^(?:Time limit for protest|Official MotoGP Timing)", text)
    if not start or not end or start.start() >= end.start():
        return []
    body = text[start.start():end.start()]
    rows = [{"pos": int(pos), "number": int(number), "name": name.strip(), "constructor": constructor.strip()}
            for pos, number, name, constructor in _ROW.findall(body)]
    # Check every remaining body line, independently of the rider-line matcher.
    # A split number/name (or missing constructor) must not disappear from both
    # matchers and make a partial table look complete. Only grid-row numbers and
    # empty starting-position labels can remain after the full entries are consumed.
    leftovers = _ROW.sub("", body)
    if (not _unique_rows(rows) or len(rows) != len(_RIDER_LINE.findall(body))
            or any(line.strip() and not re.fullmatch(r"\d{1,2}", line.strip()) for line in leftovers.splitlines())):
        return []
    return sorted(rows, key=lambda row: row["pos"])


def _name_key(value):
    return "".join(char for char in unicodedata.normalize("NFKD", value).casefold() if char.isalnum())


def verified_grid(payload, document_rows, identity):
    rows = normalize_json(payload)
    if not rows or not _unique_rows(document_rows):
        return {}
    riders = {row["number"]: row for row in rows}
    if any(row["number"] not in riders or _name_key(row["name"]) != _name_key(riders[row["number"]]["name"])
           for row in document_rows):
        return {}
    # Real QAT/GER responses still include extra riders absent from the official
    # document. Do not put them back or overwrite the document's revised positions.
    return {**identity, "kind": "official", "complete": True, "authority": "session_grid_pdf",
            "json_positions_match": all(row["pos"] == riders[row["number"]]["pos"] for row in document_rows),
            "rows": [{**riders[row["number"]], **row} for row in document_rows]}


def for_card(grid, weekend, sessions):
    """Drop a stale cross-event/session grid even if the surrounding cache is valid."""
    if (not isinstance(grid, dict) or grid.get("complete") is not True or grid.get("kind") != "official"
            or grid.get("target_type") != "RAC" or not weekend.get("id")
            or grid.get("event_id") != weekend["id"] or not _unique_rows(grid.get("rows"))):
        return {}
    if not any(isinstance(session, dict) and session.get("id") == grid.get("session_id")
               and session.get("type") == "RAC" for session in sessions):
        return {}
    return grid


def fetch(client, deadline, identity, now=None):
    payload = client.request_json("GET", API,
                                  params={"season": identity["season"], "shortname": identity["short"], "category": "MotoGP"},
                                  timeout=extra_feeds.remaining(deadline), max_bytes=JSON_MAX_BYTES).data
    # An unpublished or invalid JSON grid needs no document download.
    if not normalize_json(payload):
        return {"identity": identity, "grid": {}}
    content = client.request_bytes("GET", identity["source_url"], timeout=extra_feeds.remaining(deadline),
                                   max_bytes=PDF_MAX_BYTES).data
    grid = verified_grid(payload, parse_document(content), identity)
    if grid:
        grid.update(verified_at=(now or datetime.now(timezone.utc)).isoformat(),
                    source_sha256=hashlib.sha256(content).hexdigest())
    return {"identity": identity, "grid": grid}


def load(plugin, settings, data, now):
    """Optional independent cache: a grid failure cannot discard results/standings."""
    identity = target(data)
    if not identity:
        return {}, ""
    version = f"{VERSION}:{identity['season']}:{identity['event_id']}:{identity['session_id']}"
    loaded, state = extra_feeds.load(plugin, settings, "motogp_grid", version,
                                    lambda client, deadline: fetch(client, deadline, identity, now),
                                    TTL_SECONDS, now, label="MOTOGP GRID")
    weekend = data.get("current") or data.get("next") or {}
    grid = for_card(loaded.get("grid"), weekend, data.get("sessions") or [])
    return ({**grid, "state": state} if grid else {}), state
