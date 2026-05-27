import csv
import random
import re
from pathlib import Path
from datetime import datetime, timedelta

CSV_FIELDS = ["time", "time_human", "full_quote", "book_title", "author_name", "sfw"]


def find_candidates(csv_path: Path, hhmm: str, allow_nsfw: bool) -> list[dict]:
    """Return all rows matching the exact HH:MM time, optionally filtering NSFW."""
    matches = []
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
            if row["time"] != hhmm:
                continue
            if not allow_nsfw and row["sfw"] == "nsfw":
                continue
            matches.append(row)
    return matches


def _shift_minute(hhmm: str, delta: int) -> str:
    base = datetime.strptime(hhmm, "%H:%M")
    shifted = base + timedelta(minutes=delta)
    return shifted.strftime("%H:%M")


def resolve_with_fallback(csv_path: Path, hhmm: str, allow_nsfw: bool):
    """Return (rows, used_time). Tries HH:MM, then -1, then +1. Returns ([], None) if all empty."""
    for delta in (0, -1, 1):
        candidate_time = _shift_minute(hhmm, delta)
        rows = find_candidates(csv_path, candidate_time, allow_nsfw)
        if rows:
            return rows, candidate_time
    return [], None


# Translation table for smart punctuation
_SMART_PUNCT_MAP = {
    "‘": "'",  # left single quote
    "’": "'",  # right single quote
    "´": "'",  # acute accent
    "“": '"',  # left double quote
    "”": '"',  # right double quote
    "—": "-",  # em dash
    "–": "-",  # en dash
}


def sanitize(text: str) -> str:
    out = text.replace("<br/>", " ").replace("<br />", " ").replace("<br>", " ")
    out = out.replace("\xa0", " ")  # non-breaking space
    for smart, normal in _SMART_PUNCT_MAP.items():
        out = out.replace(smart, normal)
    return out.encode("ascii", "ignore").decode("utf-8")


def pick_quote(rows: list[dict], strategy: str, seed_key: str) -> dict:
    if not rows:
        raise ValueError("pick_quote called with empty rows")
    if strategy == "random":
        return random.choice(rows)
    if strategy == "daily":
        rng = random.Random(seed_key)
        return rng.choice(rows)
    # shortest (default)
    return min(rows, key=lambda r: len(r["full_quote"]))


HIGHLIGHT_OPEN = '<span class="litclock-highlight">'
HIGHLIGHT_CLOSE = "</span>"


def wrap_highlight(quote: str, time_human: str) -> str:
    pattern = re.compile(re.escape(time_human), re.IGNORECASE)
    return pattern.sub(lambda m: f"{HIGHLIGHT_OPEN}{m.group()}{HIGHLIGHT_CLOSE}", quote, count=1)
