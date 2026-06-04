import csv
import random
import re
from pathlib import Path
from datetime import datetime, timedelta

CSV_FIELDS = ["time", "time_human", "full_quote", "book_title", "author_name", "sfw"]


def _read_rows(csv_path: Path, allow_nsfw: bool) -> list[dict]:
    rows = []
    with open(csv_path, "r", encoding="utf-8") as f:
        reader = csv.DictReader(
            f,
            fieldnames=CSV_FIELDS,
            delimiter="|",
            lineterminator="\n",
            quotechar=None,
            quoting=csv.QUOTE_NONE,
        )
        for row in reader:
            if not row.get("time"):
                continue
            if not allow_nsfw and row.get("sfw") == "nsfw":
                continue
            rows.append(row)
    return rows


def _shift_minute(hhmm: str, delta: int) -> str:
    base = datetime.strptime(hhmm, "%H:%M")
    shifted = base + timedelta(minutes=delta)
    return shifted.strftime("%H:%M")


def _nearest_clock_keys(hhmm: str) -> list[str]:
    hour, minute = [int(part) for part in hhmm.split(":")]
    keys = []

    if minute <= 10:
        keys.append(f"{hour:02d}:00")
    elif minute >= 50:
        keys.append(f"{(hour + 1) % 24:02d}:00")

    if 20 <= minute <= 40:
        keys.append(f"{hour:02d}:30")

    if 8 <= minute <= 22:
        keys.append(f"{hour:02d}:15")
    elif 38 <= minute <= 52:
        keys.append(f"{hour:02d}:45")

    return keys


def _shichen_key(hour: int) -> str:
    if hour in (23, 0):
        return "period:zi"
    return [
        "period:chou",
        "period:yin",
        "period:mao",
        "period:chen",
        "period:si",
        "period:wu",
        "period:wei",
        "period:shen",
        "period:you",
        "period:xu",
        "period:hai",
    ][(hour - 1) // 2]


def _geng_key(hour: int):
    if 19 <= hour <= 20:
        return "period:geng1"
    if 21 <= hour <= 22:
        return "period:geng2"
    if hour == 23 or hour == 0:
        return "period:geng3"
    if 1 <= hour <= 2:
        return "period:geng4"
    if 3 <= hour <= 4:
        return "period:geng5"
    return None


def _daypart_keys(hour: int, minute: int) -> list[str]:
    keys = []
    if 4 <= hour <= 6:
        keys.append("period:dawn")
    if 6 <= hour <= 10:
        keys.append("period:morning")
    if hour == 12 and minute <= 20:
        keys.append("period:noon")
    if 17 <= hour <= 19:
        keys.append("period:dusk")
    if hour >= 21 or hour <= 4:
        keys.append("period:night")
    return keys


def _period_keys(hhmm: str) -> list[str]:
    hour, minute = [int(part) for part in hhmm.split(":")]
    keys = [_shichen_key(hour)]
    geng = _geng_key(hour)
    if geng:
        keys.append(geng)
    keys.extend(_daypart_keys(hour, minute))
    return keys


def _find_by_keys(rows: list[dict], keys: list[str]) -> list[dict]:
    wanted = set(keys)
    return [row for row in rows if row.get("time") in wanted]


def resolve_with_fallback(csv_path: Path, hhmm: str, allow_nsfw: bool):
    """Return (rows, used_key).

    Match order favors precision:
    exact minute, +/- one minute, nearby quarter/half/hour, then traditional
    Chinese time periods such as shichen and geng.
    """
    rows = _read_rows(csv_path, allow_nsfw)

    for key in [_shift_minute(hhmm, delta) for delta in (0, -1, 1)]:
        matches = _find_by_keys(rows, [key])
        if matches:
            return matches, key

    for key in _nearest_clock_keys(hhmm):
        matches = _find_by_keys(rows, [key])
        if matches:
            return matches, key

    period_keys = _period_keys(hhmm)
    matches = _find_by_keys(rows, period_keys)
    if matches:
        return matches, ",".join(period_keys)

    return [], None


_SMART_PUNCT_MAP = {
    "\u3000": " ",
    "\xa0": " ",
    "“": "「",
    "”": "」",
}


def sanitize(text: str) -> str:
    out = text or ""
    out = out.replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ")
    for smart, normal in _SMART_PUNCT_MAP.items():
        out = out.replace(smart, normal)
    out = re.sub(r"\s+", " ", out)
    return out.strip()


def pick_quote(rows: list[dict], strategy: str, seed_key: str) -> dict:
    if not rows:
        raise ValueError("pick_quote called with empty rows")
    if strategy in ("source_random", "source_daily"):
        groups = _source_groups(rows)
        if strategy == "source_daily":
            rng = random.Random(seed_key)
            return rng.choice(rng.choice(groups))
        return random.choice(random.choice(groups))
    if strategy == "random":
        return random.choice(rows)
    if strategy == "daily":
        rng = random.Random(seed_key)
        return rng.choice(rows)
    return min(rows, key=lambda row: len(row["full_quote"]))


def _source_groups(rows: list[dict]) -> list[list[dict]]:
    grouped = {}
    for row in rows:
        key = (
            (row.get("book_title") or "").strip(),
            (row.get("author_name") or "").strip(),
        )
        grouped.setdefault(key, []).append(row)
    return list(grouped.values()) or [rows]
