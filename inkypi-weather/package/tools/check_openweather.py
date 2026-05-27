import os
import sys
import argparse
from typing import Optional

import requests
from dotenv import load_dotenv


ONECALL_FREE_DAILY_MAX = 1000
ONECALL_DAILY_LIMIT_DEFAULT = 900
ONECALL_MIN_SECONDS_DEFAULT = 1800


def read_env_int(name: str, default: int, minimum: Optional[int] = None, maximum: Optional[int] = None) -> int:
    raw_value = os.getenv(name)
    try:
        value = int(raw_value) if raw_value not in (None, "") else default
    except ValueError:
        value = default
    if minimum is not None:
        value = max(minimum, value)
    if maximum is not None:
        value = min(maximum, value)
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description="Check OpenWeather One Call 3.0 access without printing the API key.")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Send one live One Call 3.0 request. This consumes one OpenWeather One Call API call.",
    )
    args = parser.parse_args()

    load_dotenv(".env")
    daily_limit = read_env_int(
        "OPENWEATHER_ONECALL_DAILY_LIMIT",
        ONECALL_DAILY_LIMIT_DEFAULT,
        minimum=0,
        maximum=ONECALL_FREE_DAILY_MAX,
    )
    min_seconds = read_env_int(
        "OPENWEATHER_ONECALL_MIN_SECONDS",
        ONECALL_MIN_SECONDS_DEFAULT,
        minimum=600,
    )
    print(f"guard_daily_limit {daily_limit}")
    print(f"guard_min_seconds {min_seconds}")

    if not args.live:
        print("status no_request_sent")
        print("message add -Live to spend exactly one One Call API request for validation")
        return 0

    key = os.getenv("OPEN_WEATHER_MAP_SECRET")
    if not key:
        print("status missing_key")
        return 1

    print("notice live_request_consumes_one_onecall_call")

    session = requests.Session()
    session.trust_env = False

    try:
        response = session.get(
            "https://api.openweathermap.org/data/3.0/onecall",
            params={
                "lat": "37.7749",
                "lon": "-122.4194",
                "units": "imperial",
                "exclude": "minutely,alerts",
                "appid": key,
            },
            timeout=20,
        )
        data = response.json() if response.headers.get("content-type", "").startswith("application/json") else {}
        print(f"status {response.status_code}")
        print("fields " + ",".join(sorted(list(data.keys()))[:8]))
        print("message " + ("ok" if response.status_code == 200 else str(data.get("message", ""))))
        return 0
    except Exception as exc:
        print("status request_failed")
        print("message " + type(exc).__name__)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
