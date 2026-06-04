from __future__ import annotations

import json
import urllib.parse
from pathlib import Path

import requests


ENV_CANDIDATES = [
    Path("/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env"),
    Path("/usr/local/inkypi/.env"),
    Path("/usr/local/inkypi/src/.env"),
    Path("inkypi-weather/package/InkyPi/.env"),
    Path(".env"),
]


def load_key() -> str:
    for path in ENV_CANDIDATES:
        if not path.exists():
            continue
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            key, value = line.split("=", 1)
            if key.strip() in {"Riot_KEY", "RIOT_API_KEY", "RIOT_KEY"}:
                return value.strip().strip("\"'")
    return ""


def report(label: str, response: requests.Response):
    print(label, "status", response.status_code, "bytes", len(response.content))
    if response.headers.get("X-App-Rate-Limit"):
        print(label, "app_rate", response.headers.get("X-App-Rate-Limit"))
    if response.status_code != 200:
        try:
            print(label, "error", response.json().get("status", {}).get("message"))
        except Exception:
            print(label, "error_text", response.text[:120])
        return None
    data = response.json()
    if isinstance(data, dict):
        print(label, "fields", ",".join(list(data.keys())[:18]))
    elif isinstance(data, list):
        fields = ",".join(list(data[0].keys())[:18]) if data and isinstance(data[0], dict) else ""
        print(label, "list_len", len(data), "item_fields", fields)
    return data


def get(label: str, url: str, headers: dict[str, str], params: dict[str, object] | None = None):
    try:
        response = requests.get(url, headers=headers, params=params or {}, timeout=25)
    except Exception as exc:
        print(label, "exception", type(exc).__name__, str(exc)[:120])
        return None
    return report(label, response)


def main():
    api_key = load_key()
    print("key_present", bool(api_key), "key_len", len(api_key) if api_key else 0)
    headers = {"X-Riot-Token": api_key}
    region = "asia"
    platform = "kr"
    game_name = "Hide on bush"
    tag_line = "KR1"

    account = get(
        "account_by_riot_id",
        f"https://{region}.api.riotgames.com/riot/account/v1/accounts/by-riot-id/"
        f"{urllib.parse.quote(game_name)}/{urllib.parse.quote(tag_line)}",
        headers,
    )
    puuid = (account or {}).get("puuid")
    if not puuid:
        return

    summoner = get(
        "summoner_by_puuid",
        f"https://{platform}.api.riotgames.com/lol/summoner/v4/summoners/by-puuid/{puuid}",
        headers,
    )
    summoner_id = (summoner or {}).get("id")
    if summoner_id:
        get(
            "league_entries",
            f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-summoner/{summoner_id}",
            headers,
        )
    get("league_entries_by_puuid", f"https://{platform}.api.riotgames.com/lol/league/v4/entries/by-puuid/{puuid}", headers)
    get("challenge_player_data", f"https://{platform}.api.riotgames.com/lol/challenges/v1/player-data/{puuid}", headers)
    get("champion_rotations", f"https://{platform}.api.riotgames.com/lol/platform/v3/champion-rotations", headers)
    get("lol_status", f"https://{platform}.api.riotgames.com/lol/status/v4/platform-data", headers)

    get(
        "mastery_top",
        f"https://{platform}.api.riotgames.com/lol/champion-mastery/v4/champion-masteries/by-puuid/{puuid}/top",
        headers,
        {"count": 5},
    )
    match_ids = get(
        "match_ids",
        f"https://{region}.api.riotgames.com/lol/match/v5/matches/by-puuid/{puuid}/ids",
        headers,
        {"start": 0, "count": 3},
    )
    if match_ids:
        match = get("match_detail", f"https://{region}.api.riotgames.com/lol/match/v5/matches/{match_ids[0]}", headers)
        if isinstance(match, dict):
            info = match.get("info", {})
            print("match_info_fields", ",".join(list(info.keys())[:20]))
            participants = info.get("participants") or []
            player = next((row for row in participants if row.get("puuid") == puuid), None)
            if player:
                sample = {
                    key: player.get(key)
                    for key in [
                        "championName",
                        "kills",
                        "deaths",
                        "assists",
                        "win",
                        "totalMinionsKilled",
                        "goldEarned",
                        "teamPosition",
                        "riotIdGameName",
                        "riotIdTagline",
                    ]
                }
                print("participant_sample", json.dumps(sample, ensure_ascii=False))
    get("active_game", f"https://{platform}.api.riotgames.com/lol/spectator/v5/active-games/by-summoner/{puuid}", headers)


if __name__ == "__main__":
    main()
