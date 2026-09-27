"""Compatibility for ESPN scoreboards that reject a multi-day dates parameter."""

from datetime import datetime, timedelta
import time

from utils.http_client import create_single_attempt_http_client


def fetch_scoreboard(session, url, params):
    response = session.get(url, params=params,
                           headers={"Accept":"application/json", "User-Agent":"InkyPi/1.0"}, timeout=20)
    dates = str(params.get("dates") or "")
    if getattr(response, "status_code", None) != 400 or "-" not in dates:
        response.raise_for_status()
        return response.json()
    response.close()
    start_text, end_text = dates.split("-", 1)
    start = datetime.strptime(start_text, "%Y%m%d").date()
    end = datetime.strptime(end_text, "%Y%m%d").date()
    if not 0 <= (end - start).days <= 21:
        raise ValueError("ESPN daily fallback range exceeds the supported window")
    deadline = time.monotonic() + 30
    merged, events, seen = {}, [], set()
    with create_single_attempt_http_client() as client:
        for offset in range((end - start).days + 1):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("ESPN daily fallback exhausted its request budget")
            day = (start + timedelta(days=offset)).strftime("%Y%m%d")
            payload = client.request_json("GET", url, params={**params, "dates":day},
                                          timeout=min(5, remaining), max_bytes=4*1024*1024).data
            if not isinstance(payload, dict) or not isinstance(payload.get("events", []), list):
                raise ValueError("ESPN daily scoreboard has an invalid events payload")
            if not merged:
                merged = dict(payload)
            for event in payload.get("events", []):
                identity = str(event.get("id") or (str(event.get("date")) + str(event.get("name"))))
                if identity not in seen:
                    seen.add(identity)
                    events.append(event)
    return {**merged, "events":events}
