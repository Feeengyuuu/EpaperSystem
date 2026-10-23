"""Who the pro tracker follows: twenty LoL pros' solo queue accounts and HLTV's Top 20 CS2 players.

LoL accounts are the best solo queue account each pro is tied to on lolprofile.net/pros
(which keeps only accounts public pro records tie to the player), checked 2026-10-23.
The owner's own recorded accounts come first and keep their saved Riot IDs; the roster
fills the rest up to twenty. The CS2 list is HLTV's Top 20 players of 2025, used when
HLTV's own page cannot be read.
"""

from __future__ import annotations

TRACKED_LOL_PLAYERS = 20

# player, team, role, game name, tag line, platform route, regional route
LOL_ROSTER = (
    ("Faker", "T1", "MID", "Hide on bush", "KR1", "kr", "asia"),
    ("Bin", "BLG", "TOP", "BLG 온", "KR1", "kr", "asia"),
    ("ShowMaker", "DK", "MID", "DK ShowMaker", "KR1", "kr", "asia"),
    ("Chovy", "GEN", "MID", "허거덩", "0303", "kr", "asia"),
    ("Keria", "T1", "SUPPORT", "Ciro", "KR10", "kr", "asia"),
    ("Peyz", "T1", "BOTTOM", "Peyz", "KR11", "kr", "asia"),
    ("Kiin", "GEN", "TOP", "kiin", "KR1", "kr", "asia"),
    ("Canyon", "GEN", "JUNGLE", "JUGKlNG", "kr", "kr", "asia"),
    ("Ruler", "GEN", "BOTTOM", "강 철", "샤 넬", "kr", "asia"),
    ("Duro", "GEN", "SUPPORT", "Duro", "Gen", "kr", "asia"),
    ("Zeus", "HLE", "TOP", "Athene", "lll", "kr", "asia"),
    ("Zeka", "HLE", "MID", "suis", "kr7", "kr", "asia"),
    ("Gumayusi", "HLE", "BOTTOM", "HLE Gumayusi", "0298", "kr", "asia"),
    ("Kanavi", "HLE", "JUNGLE", "PoAtan", "ASP", "kr", "asia"),
    ("Delight", "HLE", "SUPPORT", "플레이리스트겨울", "KR1", "kr", "asia"),
    ("Viper", "BLG", "BOTTOM", "Blue", "KR33", "kr", "asia"),
    ("ON", "BLG", "SUPPORT", "키보드에음료쏟아서고장내는이도윤", "KR1", "kr", "asia"),
    ("Lucid", "DK", "JUNGLE", "DK Lucid", "KR1", "kr", "asia"),
    ("JackeyLove", "TES", "BOTTOM", "qweasdqweasdqw", "KR1", "kr", "asia"),
    ("Caps", "G2", "MID", "G2 Caps", "1323", "euw1", "europe"),
)

ROLE_LABELS = {"TOP": "上单", "JUNGLE": "打野", "MID": "中单", "BOTTOM": "下路", "SUPPORT": "辅助"}

# lolesports.com team ids whose official rosters carry each pro's photo.
LOLESPORTS_TEAM_SLUGS = {"T1": "t1", "GEN": "geng", "HLE": "hanwha-life-esports", "DK": "dwg-kia",
                         "BLG": "bilibili-gaming", "TES": "top-esports", "G2": "g2-esports"}

# rank, nickname, real name, country code (HLTV Top 20 players of 2025, revealed January 2026)
CS2_TOP20_2025 = (
    (1, "ZywOo", "Mathieu Herbaut", "FR"),
    (2, "donk", "Danil Kryshkovets", "RU"),
    (3, "ropz", "Robin Kool", "EE"),
    (4, "m0NESY", "Ilya Osipov", "RU"),
    (5, "sh1ro", "Dmitry Sokolov", "RU"),
    (6, "molodoy", "Danil Golubenko", "KZ"),
    (7, "flameZ", "Shahar Shushan", "IL"),
    (8, "frozen", "David Čerňanský", "SK"),
    (9, "KSCERATO", "Kaike Cerato", "BR"),
    (10, "Spinx", "Lotan Giladi", "IL"),
    (11, "Twistzz", "Russel Van Dulken", "CA"),
    (12, "mezii", "William Merriman", "GB"),
    (13, "Senzu", "Azbayar Munkhbold", "MN"),
    (14, "XANTARES", "Ismailcan Dörtkardeş", "TR"),
    (15, "YEKINDAR", "Mareks Gaļinskis", "LV"),
    (16, "xertioN", "Dorian Berman", "IL"),
    (17, "torzsi", "Ádám Torzsás", "HU"),
    (18, "NiKo", "Nikola Kovač", "BA"),
    (19, "iM", "Mihai Ivan", "RO"),
    (20, "b1t", "Valeriy Vakhovskiy", "UA"),
)


def roster_entry(player, team, role, game_name, tag_line, platform, region):
    return {"label": player, "team": team, "role": role, "gameName": game_name, "tagLine": tag_line,
            "platformRoute": platform, "regionalRoute": region}


def tracked_lol_accounts(saved):
    """The owner's recorded accounts first, then the roster's other pros, up to twenty players.

    ``saved`` are parsed setting entries (label, gameName, tagLine, routes); each is matched
    to its roster player by label to add the team and role.
    """
    by_player = {row[0].casefold(): row for row in LOL_ROSTER}
    accounts, seen = [], set()
    for entry in saved:
        key = str(entry.get("label") or "").casefold()
        row = by_player.get(key)
        account = dict(entry)
        account.setdefault("team", row[1] if row else "")
        account.setdefault("role", row[2] if row else "")
        accounts.append(account)
        seen.add(key)
    for row in LOL_ROSTER:
        if len(accounts) >= max(TRACKED_LOL_PLAYERS, len(saved)):
            break
        if row[0].casefold() not in seen:
            accounts.append(roster_entry(*row))
            seen.add(row[0].casefold())
    return accounts
