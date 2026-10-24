"""The pro tracker behind LoLInfo: who is shown next and the boards kept fresh around them.

Each render shows one player drawn at random from the forty tracked (twenty LoL pros'
accounts and HLTV's Top 20 CS2 players): a shuffle bag, so everyone appears once per
round and a new round never starts with the player just shown.

How fresh each piece is (the owner's update cadence):
- the shown LoL account's profile, matches and live game: the instance's refreshMinutes;
  a pro with more than one account is shown on the one they played on most recently;
- the LoL ladder of all twenty: up to LOL_BOARD_BATCH stale accounts (older than
  LOL_BOARD_MINUTES) are re-read on every LoL render, one league call each, and each
  pro's row shows their best-ranked account;
- HLTV's Top 20 list: weekly; the shown CS2 player's page: CS2_PLAYER_HOURS; the CS2
  board's ratings: CS2_BOARD_BATCH stale players (older than CS2_BOARD_HOURS) per CS2 render.
This keeps a render under ~20 Riot calls, well inside a development key's 100 per 2 minutes.
"""

from __future__ import annotations

from datetime import datetime
import logging
import random
import time
import urllib.parse

from runtime.refresh_contracts import TaskCancelled
from utils.http_client import get_http_session

from plugins.sports_dashboard.common import LOLESPORTS_API_KEY

from . import hltv_players
from .pro_roster import CS2_TOP20_2025, LOLESPORTS_TEAM_SLUGS, tracked_lol_accounts

logger = logging.getLogger(__name__)

POOL_STATE = "pro_pool_rotation.json"
PHOTO_STATE = "pro_lol_photos.json"
PHOTO_DAYS = 7
LOLESPORTS_TEAMS_URL = "https://esports-api.lolesports.com/persisted/gw/getTeams"
LOL_BOARD_STATE = "pro_lol_board.json"
CS2_STATE = "pro_cs2_state.json"
LOL_BOARD_BATCH = 4
LOL_BOARD_MINUTES = 90
PUUID_DAYS = 30
CS2_TOP20_DAYS = 7
CS2_PLAYER_HOURS = 6
CS2_BOARD_BATCH = 2
CS2_BOARD_HOURS = 24
# HLTV answers bursts with a 403/429 challenge; after one, leave it alone for this long.
HLTV_BACKOFF_HOURS = 6
RANK_ORDER = {"CHALLENGER": 9, "GRANDMASTER": 8, "MASTER": 7, "DIAMOND": 6, "EMERALD": 5, "PLATINUM": 4,
              "GOLD": 3, "SILVER": 2, "BRONZE": 1, "IRON": 0}
DIVISIONS = {"I": 3, "II": 2, "III": 1, "IV": 0}


class ProTrackerMixin:
    # -- the pool and the random pick ----------------------------------------------
    def _tracked_lol(self, settings):
        accounts = []
        for entry in tracked_lol_accounts(self._pro_accounts(settings)):
            parsed = self._pro_account_from_entry(entry)
            if parsed:
                alts = [alt for alt in (self._pro_account_from_entry(item) for item in entry.get("alts") or [])
                        if alt and alt["id"] != parsed["id"]]
                accounts.append({**parsed, "team": entry.get("team", ""), "role": entry.get("role", ""), "alts": alts})
        return accounts

    @staticmethod
    def _account_options(account):
        return [account] + list(account.get("alts") or [])

    def _cs2_roster(self, state=None):
        top20 = (state or self._read_json(self._cache_dir() / CS2_STATE, {})).get("top20") or []
        if len(top20) >= 10:
            return top20
        return [dict(row) for row in CS2_TOP20_2025]

    def _tracked_pool(self, settings):
        picks = [{"kind": "lol", "id": "lol|" + account["id"], "account": account}
                 for account in self._tracked_lol(settings)]
        if self._enabled(settings.get("includeCs2"), default=True):
            picks += [{"kind": "cs2", "id": "cs2|" + row["nick"].casefold(), "player": row}
                      for row in self._cs2_roster()]
        return picks

    def _pick(self, settings, advance=True):
        """The player to show: the next from the shuffle bag, or the last one shown when not advancing."""
        pool = self._tracked_pool(settings)
        if not pool:
            return None
        by_id = {item["id"]: item for item in pool}
        path = self._cache_dir() / POOL_STATE
        state = self._read_json(path, {})
        if not advance:
            return {**by_id.get(state.get("last"), pool[0]), "position": state.get("position", 1), "size": len(pool)}
        ids = [item["id"] for item in pool]
        # A pro whose every account Riot no longer knows sits out until the ladder refresh finds one again.
        missing = self._missing_lol_ids(self._tracked_lol(settings))
        queue = [item for item in state.get("queue") or [] if item in by_id and item not in missing]
        if sorted(state.get("pool_ids") or []) != sorted(ids) or not queue:
            queue = [item for item in ids if item not in missing] or ids[:]
            random.shuffle(queue)
            if len(queue) > 1 and queue[0] == state.get("last"):
                queue.append(queue.pop(0))
        selected = queue.pop(0)
        position = len(ids) - len(queue)
        self._write_json(path, {"pool_ids": ids, "queue": queue, "last": selected, "position": position,
                                "updated_ts": time.time()})
        return {**by_id[selected], "position": position, "size": len(ids)}

    # -- LoL pro photos ------------------------------------------------------------------
    def _lol_photo(self, account, refresh=True):
        """The pro's official photo URL from their team's lolesports roster (re-read weekly)."""
        slug = LOLESPORTS_TEAM_SLUGS.get(str(account.get("team") or "").upper())
        if not slug:
            return ""
        path = self._cache_dir() / PHOTO_STATE
        state = self._read_json(path, {})
        team = state.get(slug) or {}
        if refresh and time.time() - float(team.get("ts") or 0) > PHOTO_DAYS * 86400:
            team = {"ts": time.time(), "players": team.get("players") or {}}
            try:
                response = get_http_session().get(LOLESPORTS_TEAMS_URL, params={"hl": "en-US", "id": slug},
                                                  headers={"x-api-key": LOLESPORTS_API_KEY}, timeout=20)
                response.raise_for_status()
                teams = (response.json().get("data") or {}).get("teams") or []
                team["players"] = {str(player.get("summonerName") or "").strip().casefold(): player.get("image")
                                   for player in (teams[0].get("players") if teams else []) or []
                                   if player.get("image")} or team["players"]
            except TaskCancelled:
                raise
            except Exception as exc:
                logger.warning("lolesports roster %s unavailable: %s", slug, exc)
                team["ts"] = time.time() - PHOTO_DAYS * 43200  # retry in half the usual wait
            state[slug] = team
            self._write_json(path, state)
        return (team.get("players") or {}).get(str(account.get("label") or "").casefold(), "")

    # -- LoL ladder of the tracked accounts -------------------------------------------
    def _riot_json(self, api_key, scope, path, params=None):
        response = get_http_session().get(f"https://{scope}.api.riotgames.com{path}", params=params or {},
                                          headers={"X-Riot-Token": api_key}, timeout=20)
        if response.status_code in (403, 404):
            return None
        response.raise_for_status()
        return response.json()

    @staticmethod
    def _rank_score(entry):
        if not entry:
            return -1
        return (RANK_ORDER.get(str(entry.get("tier") or "").upper(), -1) * 4
                + DIVISIONS.get(str(entry.get("rank") or ""), 0)) * 10000 + int(entry.get("lp") or 0)

    def _lol_board(self, settings, device_config, featured=None, refresh=True):
        """Every tracked account's solo queue standing, best first, with the stalest few re-read."""
        accounts = self._tracked_lol(settings)
        path = self._cache_dir() / LOL_BOARD_STATE
        state = self._read_json(path, {})
        now = time.time()
        if featured:
            account_id, ranked, active = featured
            entry = dict(state.get(account_id) or {})
            entry.update(self._ladder_entry(ranked), checked_ts=now, in_game=bool(active))
            state[account_id] = entry
        api_key = self._riot_key(settings, device_config) if refresh else ""
        options = [option for account in accounts for option in self._account_options(account)]
        if api_key:
            stale = sorted((account for account in options if account["id"] != (featured or ("",))[0]),
                           key=lambda account: (state.get(account["id"]) or {}).get("checked_ts", 0))
            for account in stale[:LOL_BOARD_BATCH]:
                entry = dict(state.get(account["id"]) or {})
                if now - float(entry.get("checked_ts") or 0) < LOL_BOARD_MINUTES * 60:
                    continue
                try:
                    entry.update(self._read_ladder_entry(api_key, account, entry, now))
                except TaskCancelled:
                    raise
                except Exception as exc:
                    logger.warning("Pro ladder refresh failed for %s: %s", account["label"], exc)
                    entry["checked_ts"] = now - LOL_BOARD_MINUTES * 30  # retry in half the usual wait
                state[account["id"]] = entry
        if featured or api_key:
            self._write_json(path, {key: value for key, value in state.items()
                                    if key in {option["id"] for option in options}})
        rows = []
        for account in accounts:
            entries = [(option, state.get(option["id"]) or {}) for option in self._account_options(account)]
            ranked = [pair for pair in entries if pair[1].get("tier") and not pair[1].get("missing")]
            option, entry = max(ranked, key=lambda pair: self._rank_score(pair[1])) if ranked else entries[0]
            rows.append({"id": account["id"], "player": account["label"], "team": account.get("team", ""),
                         "role": account.get("role", ""), "riot_id": f"{option['gameName']}#{option['tagLine']}",
                         "tier": entry.get("tier"), "rank": entry.get("rank"), "lp": entry.get("lp"),
                         "wins": entry.get("wins"), "losses": entry.get("losses"),
                         "in_game": any(pair[1].get("in_game") for pair in entries),
                         "missing": all(pair[1].get("missing") for pair in entries),
                         "checked_ts": entry.get("checked_ts")})
        rows.sort(key=lambda row: self._rank_score(row), reverse=True)
        return rows

    def _missing_lol_ids(self, accounts):
        state = self._read_json(self._cache_dir() / LOL_BOARD_STATE, {})

        def missing(option):
            entry = state.get(option["id"])
            return isinstance(entry, dict) and entry.get("missing")

        return {"lol|" + account["id"] for account in accounts
                if all(missing(option) for option in self._account_options(account))}

    def _freshest_lol_account(self, settings, device_config, account, refresh=True):
        """The pro's account they last played on (by the newest match id); the main one when unsure.

        A theme redraw keeps the account the last live render chose, so it finds that render's cache.
        """
        options = self._account_options(account)
        if len(options) == 1:
            return account
        path = self._cache_dir() / LOL_BOARD_STATE
        state = self._read_json(path, {})
        chosen = next((option for option in options
                       if option["id"] == (state.get(account["id"]) or {}).get("shown_id")), account)
        api_key = self._riot_key(settings, device_config) if refresh else ""
        if api_key:
            now, newest = time.time(), -1
            for option in options:
                entry = dict(state.get(option["id"]) or {})
                if entry.get("missing"):
                    continue
                try:
                    entry = self._with_puuid(api_key, option, entry, now)
                    if entry.get("missing"):
                        state[option["id"]] = entry
                        continue
                    ids = self._riot_json(api_key, option["regionalRoute"],
                                          f"/lol/match/v5/matches/by-puuid/{entry['puuid']}/ids",
                                          {"start": 0, "count": 1}) or []
                except TaskCancelled:
                    raise
                except Exception as exc:
                    logger.warning("Latest match unavailable for %s %s: %s", option["label"], option["gameName"], exc)
                    continue
                state[option["id"]] = entry
                number = self._match_number(ids[0]) if ids else -1
                if number > newest:
                    chosen, newest = option, number
            state[account["id"]] = {**(state.get(account["id"]) or {}), "shown_id": chosen["id"]}
            self._write_json(path, state)
        return {**account, **{key: chosen[key] for key in ("id", "gameName", "tagLine", "platformRoute",
                                                           "regionalRoute")},
                "player_id": account["id"]}

    @staticmethod
    def _match_number(match_id):
        """Riot's match id (like KR_7834512345) as its number, which grows with time within a region."""
        try:
            return int(str(match_id).rsplit("_", 1)[-1])
        except ValueError:
            return -1

    def _mark_lol_missing(self, account):
        """Record that Riot answered 404 for this Riot ID, for the board and the next picks."""
        path = self._cache_dir() / LOL_BOARD_STATE
        state = self._read_json(path, {})
        state[account["id"]] = {**(state.get(account["id"]) or {}), "missing": True, "checked_ts": time.time()}
        self._write_json(path, state)

    @staticmethod
    def _ladder_entry(ranked):
        ranked = ranked or {}
        return {"tier": ranked.get("tier"), "rank": ranked.get("rank"), "lp": ranked.get("leaguePoints"),
                "wins": ranked.get("wins"), "losses": ranked.get("losses")}

    def _with_puuid(self, api_key, account, entry, now):
        """``entry`` with the account's PUUID (looked up again monthly), or marked missing when Riot has none."""
        if entry.get("puuid") and now - float(entry.get("puuid_ts") or 0) <= PUUID_DAYS * 86400:
            return entry
        found = self._riot_json(api_key, account["regionalRoute"],
                                "/riot/account/v1/accounts/by-riot-id/"
                                f"{urllib.parse.quote(account['gameName'])}/{urllib.parse.quote(account['tagLine'])}")
        if not found or not found.get("puuid"):
            return {**entry, "checked_ts": now, "missing": True}
        return {**entry, "puuid": found["puuid"], "puuid_ts": now, "missing": False}

    def _read_ladder_entry(self, api_key, account, entry, now):
        entry = self._with_puuid(api_key, account, entry, now)
        if entry.get("missing"):
            return {"checked_ts": now, "missing": True}
        leagues = self._riot_json(api_key, account["platformRoute"],
                                  f"/lol/league/v4/entries/by-puuid/{entry['puuid']}") or []
        return {**entry, **self._ladder_entry(self._best_rank(leagues)), "checked_ts": now, "missing": False}

    # -- CS2: HLTV Top 20 and player pages ---------------------------------------------
    def _cs2_data(self, pick, refresh=True):
        """The shown CS2 player's page data and the Top 20 board, refreshing what has gone stale."""
        path = self._cache_dir() / CS2_STATE
        state = self._read_json(path, {})
        players = state.setdefault("players", {})
        now = time.time()
        changed = False
        if refresh and now >= float(state.get("hltv_backoff_until") or 0):
            session = get_http_session()
            if now - float(state.get("top20_ts") or 0) > CS2_TOP20_DAYS * 86400:
                changed = True
                state["top20_ts"] = now
                for year in (datetime.now().year - 1, datetime.now().year - 2):
                    try:
                        rows = hltv_players.parse_top20(hltv_players.fetch(session, f"/players/top20/{year}"))
                    except TaskCancelled:
                        raise
                    except Exception as exc:
                        logger.warning("HLTV Top 20 %s unavailable: %s", year, exc)
                        if self._hltv_refused(exc):
                            state["hltv_backoff_until"] = now + HLTV_BACKOFF_HOURS * 3600
                            break
                        continue
                    if len(rows) >= 10:
                        state["top20"], state["top20_year"] = rows, year
                        break
            roster = self._cs2_roster(state)
            nick = pick["player"]["nick"]
            stale = sorted((row["nick"] for row in roster if row["nick"] != nick),
                           key=lambda name: (players.get(name) or {}).get("page_ts", 0))
            targets = [(nick, CS2_PLAYER_HOURS)] + [(name, CS2_BOARD_HOURS) for name in stale[:CS2_BOARD_BATCH]]
            for name, hours in targets:
                entry = dict(players.get(name) or {})
                if now - float(entry.get("page_ts") or 0) < hours * 3600 or state.get("hltv_backoff_until", 0) > now:
                    continue
                changed = True
                try:
                    entry.update(self._read_cs2_player(session, name, entry))
                    entry["page_ts"] = now
                except TaskCancelled:
                    raise
                except Exception as exc:
                    logger.warning("HLTV player %s unavailable: %s", name, exc)
                    entry["page_ts"] = now - hours * 1800  # retry in half the usual wait
                    if self._hltv_refused(exc):
                        state["hltv_backoff_until"] = now + HLTV_BACKOFF_HOURS * 3600
                players[name] = entry
        if changed:
            self._write_json(path, state)
        roster = self._cs2_roster(state)
        board = [{**row, **{key: (players.get(row["nick"]) or {}).get(key) for key in ("rating", "maps", "team")},
                  "team": (players.get(row["nick"]) or {}).get("team") or row.get("team", "")} for row in roster]
        row = next((item for item in roster if item["nick"] == pick["player"]["nick"]), pick["player"])
        return {"player": {**row, **(players.get(row["nick"]) or {})}, "board": board,
                "top20_year": state.get("top20_year") or 2025}

    @staticmethod
    def _hltv_refused(exc):
        return getattr(getattr(exc, "response", None), "status_code", None) in (403, 429)

    def _read_cs2_player(self, session, nick, entry):
        if not entry.get("location"):
            found = hltv_players.parse_search(hltv_players.fetch(session, "/search?term=" + urllib.parse.quote(nick)),
                                              nick)
            if not found:
                raise ValueError("not found in HLTV search")
            entry = {**entry, **found}
        page = hltv_players.parse_player_page(hltv_players.fetch(session, entry["location"]))
        return {**entry, **{key: value for key, value in page.items() if value not in (None, "", [], {})}}
