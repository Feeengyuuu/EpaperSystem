"""Who the pro tracker follows: twenty LoL pros' solo queue accounts and HLTV's Top 20 CS2 players.

LoL accounts are the best solo queue account each pro is tied to on lolprofile.net/pros
(which keeps only accounts public pro records tie to the player), each checked on op.gg's
pro tag and Leaguepedia's soloqueue IDs on 2026-10-23.
The owner's own recorded accounts come first and keep their saved Riot IDs; one the roster
knows is labelled with the player who owns it. The roster fills the rest up to twenty.
The CS2 list is HLTV's Top 20 players of 2025, used when HLTV's own page cannot be read.
"""

from __future__ import annotations

TRACKED_LOL_PLAYERS = 20

# player, team, role, game name, tag line, platform route, regional route
LOL_ROSTER = (
    ("Faker", "T1", "MID", "Hide on bush", "KR1", "kr", "asia"),
    ("Knight", "BLG", "MID", "BLG 온", "KR1", "kr", "asia"),
    ("Bin", "BLG", "TOP", "빈 스토리", "KR1", "kr", "asia"),
    ("ShowMaker", "DK", "MID", "DK ShowMaker", "KR1", "kr", "asia"),
    ("Chovy", "GEN", "MID", "허거덩", "0303", "kr", "asia"),
    ("Keria", "T1", "SUPPORT", "Ciro", "KR10", "kr", "asia"),
    ("Peyz", "T1", "BOTTOM", "Peyz", "KR11", "kr", "asia"),
    ("Kiin", "GEN", "TOP", "kiin", "KR1", "kr", "asia"),
    ("Canyon", "GEN", "JUNGLE", "JUGKlNG", "kr", "kr", "asia"),
    ("Ruler", "GEN", "BOTTOM", "강 철", "샤 넬", "kr", "asia"),
    ("Zeus", "HLE", "TOP", "Athene", "lll", "kr", "asia"),
    ("Zeka", "HLE", "MID", "suis", "kr7", "kr", "asia"),
    ("Gumayusi", "HLE", "BOTTOM", "HLE Gumayusi", "0298", "kr", "asia"),
    ("Kanavi", "HLE", "JUNGLE", "vinaka", "KR1", "kr", "asia"),
    ("Delight", "HLE", "SUPPORT", "플레이리스트겨울", "KR1", "kr", "asia"),
    ("Viper", "BLG", "BOTTOM", "Blue", "KR33", "kr", "asia"),
    ("ON", "BLG", "SUPPORT", "키보드에음료쏟아서고장내는이도윤", "KR1", "kr", "asia"),
    ("Lucid", "DK", "JUNGLE", "DK Lucid", "KR1", "kr", "asia"),
    ("JackeyLove", "TES", "BOTTOM", "qweasdqweasdqw", "KR1", "kr", "asia"),
    ("Caps", "G2", "MID", "G2 Caps", "1323", "euw1", "europe"),
)

# Each pro's other ranked solo queue accounts on lolprofile.net (checked 2026-10-24), as
# (game name, tag line, platform route, regional route). The ladder shows a pro's best-ranked
# account and the page the one they played on most recently.
LOL_ALT_ACCOUNTS = {
    "Kanavi": (("PoAtan", "ASP", "kr", "asia"),),
    "ShowMaker": (("MIDKING", "asd", "kr", "asia"),),
    "Lucid": (("너무재미있겠다", "KR1", "kr", "asia"),),
    "Gumayusi": (("thsorre", "2830", "kr", "asia"),),
    "Zeka": (("Kiruru", "kr7", "kr", "asia"),),
}

ROLE_LABELS = {"TOP": "上单", "JUNGLE": "打野", "MID": "中单", "BOTTOM": "下路", "SUPPORT": "辅助"}

# The league each tracked team plays in (its logo in assets/leagues), and each account server's
# flag (flagcdn code) and Chinese name.
TEAM_LEAGUES = {"T1": "lck", "GEN": "lck", "HLE": "lck", "DK": "lck", "BLG": "lpl", "TES": "lpl", "G2": "lec"}
SERVER_FLAGS = {"kr": ("kr", "韩服"), "euw1": ("eu", "欧服"), "eun1": ("eu", "欧服"), "na1": ("us", "美服"),
                "jp1": ("jp", "日服")}

# lolesports.com team ids whose official rosters carry each pro's photo.
LOLESPORTS_TEAM_SLUGS = {"T1": "t1", "GEN": "geng", "HLE": "hanwha-life-esports", "DK": "dwg-kia",
                         "BLG": "bilibili-gaming", "TES": "top-esports", "G2": "g2-esports"}

# HLTV's Top 20 players of 2025 (revealed January 2026) as its page listed them, with the team then
# and HLTV's image CDN links (reachable even when HLTV's pages are not).
CS2_TOP20_2025 = (
    {"rank": 1, "nick": "ZywOo", "name": "Mathieu Herbaut", "country": "FR", "team": "Vitality",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/ogcHrcCdzRvxbYvAz04KAN.png?ixlib=java-2.1.0&w=50&s=e1f6019aa9f274ffe45a5e99c88dbc02",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/FCjBs5LLr_Td0Vl3YocFy8.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=117%2C8%2C467%2C467&w=100&s=51058c59e53455c58f7342a0a856ae55"},
    {"rank": 2, "nick": "donk", "name": "Danil Kryshkovets", "country": "RU", "team": "Spirit",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/syrtYYKR7sBRw3ZHy1YFX7.png?ixlib=java-2.1.0&w=50&s=40e66714687bec05ea422255b1c0099e",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/sEwjjWjn36V9aqH6i07aFx.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C471%2C471&w=100&s=ab270772f724a6502c49fe8481d070af"},
    {"rank": 3, "nick": "ropz", "name": "Robin Kool", "country": "EE", "team": "Vitality",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/ogcHrcCdzRvxbYvAz04KAN.png?ixlib=java-2.1.0&w=50&s=e1f6019aa9f274ffe45a5e99c88dbc02",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/lC8JyY0y43QcoKDC41ZeEX.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=124%2C8%2C467%2C467&w=100&s=150a53c47affe94335388c4d868e6a33"},
    {"rank": 4, "nick": "m0NESY", "name": "Ilya Osipov", "country": "RU", "team": "Falcons",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/4eJSkDQINNM6Tbs4WvLzkN.png?ixlib=java-2.1.0&w=50&s=d8c857ea47046f61eca695beab0d12ef",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/e20I1I0Ld0bWn_VI31pf08.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=124%2C8%2C467%2C467&w=100&s=131da1e023f7c8bc4cdbcefc2f7b3868"},
    {"rank": 5, "nick": "sh1ro", "name": "Dmitry Sokolov", "country": "RU", "team": "Spirit",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/syrtYYKR7sBRw3ZHy1YFX7.png?ixlib=java-2.1.0&w=50&s=40e66714687bec05ea422255b1c0099e",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/0zUgFo6716hiPMYZdJPqlk.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=acaf59c5c9a9b0a038ff7a23a13cef60"},
    {"rank": 6, "nick": "molodoy", "name": "Danil Golubenko", "country": "KZ", "team": "FURIA",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/mvNQc4csFGtxXk5guAh8m1.svg?ixlib=java-2.1.0&s=11e5056829ad5d6c06c5961bbe76d20c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/oPoWLYFq87cIs2cYDo8id7.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=a5a4830a878f1952a90ed9f942daa4f5"},
    {"rank": 7, "nick": "flameZ", "name": "Shahar Shushan", "country": "IL", "team": "Vitality",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/ogcHrcCdzRvxbYvAz04KAN.png?ixlib=java-2.1.0&w=50&s=e1f6019aa9f274ffe45a5e99c88dbc02",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/bprjHKPKx2fZBXdi-Z6A_m.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C0%2C467%2C467&w=100&s=3310a220d61b41f858495c3921d036cf"},
    {"rank": 8, "nick": "frozen", "name": "David Čerňanský", "country": "SK", "team": "FaZe",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/J3CwtISw72UEuibqDq0DZt.png?ixlib=java-2.1.0&w=50&s=02a017dc6bced6da74bc8821d1100f4a",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/6wbdcMG9zPzAOert7XFLnV.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=117%2C8%2C467%2C467&w=100&s=5f5d461833ec465992650f4197c18cb8"},
    {"rank": 9, "nick": "KSCERATO", "name": "Kaike Cerato", "country": "BR", "team": "FURIA",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/mvNQc4csFGtxXk5guAh8m1.svg?ixlib=java-2.1.0&s=11e5056829ad5d6c06c5961bbe76d20c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/z0vT0V815B0MdeeKhcf44Y.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=b27fcd6631f29d7931e43b0dea521094"},
    {"rank": 10, "nick": "Spinx", "name": "Lotan Giladi", "country": "IL", "team": "MOUZ",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/IejtXpquZnE8KqYPB1LNKw.svg?ixlib=java-2.1.0&s=7fd33b8def053fbfd8fdbb58e3bdcd3c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/e21k33SYzHd4xiEclyz50o.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C0%2C467%2C467&w=100&s=1c06df936f7f88b4c1b884a019d7fc7e"},
    {"rank": 11, "nick": "Twistzz", "name": "Russel Van Dulken", "country": "CA", "team": "Liquid",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/JMeLLbWKCIEJrmfPaqOz4O.svg?ixlib=java-2.1.0&s=c02caf90234d3a3ebac074c84ba1ea62",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/ylRoZVxgFBVh7QswRGL4-k.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=7386eb0e51774a0daaab6ce4a8bca8ff"},
    {"rank": 12, "nick": "mezii", "name": "William Merriman", "country": "GB", "team": "Vitality",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/ogcHrcCdzRvxbYvAz04KAN.png?ixlib=java-2.1.0&w=50&s=e1f6019aa9f274ffe45a5e99c88dbc02",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/G3ENcoVgPQ2EovvoCisCH1.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=2769751e38fe5fb2db914d5a9b87aced"},
    {"rank": 13, "nick": "Senzu", "name": "Azbayar Munkhbold", "country": "MN", "team": "The MongolZ",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/bRk2sh_tSTO6fq1GLhgcal.png?ixlib=java-2.1.0&w=50&s=8b08e53858eb817852ae74b30a30151d",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/kvdn29qCPgSQmgCfdB-l7-.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=117%2C8%2C467%2C467&w=100&s=7e5212e61f0cbdc4b3930d83f3667380"},
    {"rank": 14, "nick": "XANTARES", "name": "Ismailcan Dörtkardeş", "country": "TR", "team": "Aurora",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/yJzPNOeXlyiniNxanYJCrv.png?ixlib=java-2.1.0&w=50&s=2c08f70c2f2f8c2024a438ddcf19bbf1",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/KoaITTJTZcHOXBQGvbhW1x.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=124%2C12%2C467%2C467&w=100&s=84996e1d38614c278e41d0f64f8de9a1"},
    {"rank": 15, "nick": "YEKINDAR", "name": "Mareks Gaļinskis", "country": "LV", "team": "FURIA",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/mvNQc4csFGtxXk5guAh8m1.svg?ixlib=java-2.1.0&s=11e5056829ad5d6c06c5961bbe76d20c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/IO3vEa2fT2qFPRlrPid7hf.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C8%2C467%2C467&w=100&s=da2b1266b74e6038851bb304e5679172"},
    {"rank": 16, "nick": "xertioN", "name": "Dorian Berman", "country": "IL", "team": "MOUZ",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/IejtXpquZnE8KqYPB1LNKw.svg?ixlib=java-2.1.0&s=7fd33b8def053fbfd8fdbb58e3bdcd3c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/f3k06p19e8890q9hq0S9Gi.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=117%2C8%2C467%2C467&w=100&s=5a6429b6b66bb1c5adf6256925af399b"},
    {"rank": 17, "nick": "torzsi", "name": "Ádám Torzsás", "country": "HU", "team": "MOUZ",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/IejtXpquZnE8KqYPB1LNKw.svg?ixlib=java-2.1.0&s=7fd33b8def053fbfd8fdbb58e3bdcd3c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/R_N5toY5QunKzIeapwFCG9.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C0%2C467%2C467&w=100&s=fe49034194fae544da85e5946d671d67"},
    {"rank": 18, "nick": "NiKo", "name": "Nikola Kovač", "country": "BA", "team": "Falcons",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/4eJSkDQINNM6Tbs4WvLzkN.png?ixlib=java-2.1.0&w=50&s=d8c857ea47046f61eca695beab0d12ef",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/ZrAcgiRTFgDyDj4k04-xAh.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=124%2C12%2C467%2C467&w=100&s=6e75c5d4381521f36ba4d80c87de98fe"},
    {"rank": 19, "nick": "iM", "name": "Mihai Ivan", "country": "RO", "team": "Natus Vincere",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/9iMirAi7ArBLNU8p3kqUTZ.svg?ixlib=java-2.1.0&s=4dd8635be16122656093ae9884675d0c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/-IvDIpLxFYKvqftxmcVmpm.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=121%2C4%2C467%2C467&w=100&s=3f4575709eb22a42abb328bfdfe0df96"},
    {"rank": 20, "nick": "b1t", "name": "Valeriy Vakhovskiy", "country": "UA", "team": "Natus Vincere",
     "team_logo": "https://img-cdn.hltv.org/teamlogo/9iMirAi7ArBLNU8p3kqUTZ.svg?ixlib=java-2.1.0&s=4dd8635be16122656093ae9884675d0c",
     "bodyshot": "https://img-cdn.hltv.org/playerbodyshot/KtDBV4_0Gm4kmZ0mZ-J1o9.png?bg=3e4c54&h=100&ixlib=java-2.1.0&rect=124%2C4%2C467%2C467&w=100&s=d38209d17161779413c3d5b5979d455f"},
)


def roster_entry(player, team, role, game_name, tag_line, platform, region):
    return {"label": player, "team": team, "role": role, "gameName": game_name, "tagLine": tag_line,
            "platformRoute": platform, "regionalRoute": region}


def alt_accounts(player):
    """The pro's other known accounts, each a parseable entry carrying the pro's label."""
    return [{"label": player, "gameName": game_name, "tagLine": tag_line, "platformRoute": platform,
             "regionalRoute": region}
            for game_name, tag_line, platform, region in LOL_ALT_ACCOUNTS.get(player, ())]


def tracked_lol_accounts(saved):
    """The owner's recorded accounts first, then the roster's other pros, up to twenty players.

    ``saved`` are parsed setting entries (label, gameName, tagLine, routes); each is matched
    to its roster player by Riot ID (which also corrects a saved label naming the wrong
    player), else by label, to add the team and role.
    """
    by_player = {row[0].casefold(): row for row in LOL_ROSTER}
    by_riot_id = {(row[3].casefold(), row[4].casefold(), row[5]): row for row in LOL_ROSTER}
    accounts, seen = [], set()
    for entry in saved:
        row = by_riot_id.get((str(entry.get("gameName") or "").casefold(),
                              str(entry.get("tagLine") or "").casefold(), entry.get("platformRoute")))
        account = dict(entry)
        if row:
            account["label"] = row[0]
        key = str(account.get("label") or "").casefold()
        row = row or by_player.get(key)
        account.setdefault("team", row[1] if row else "")
        account.setdefault("role", row[2] if row else "")
        account["alts"] = alt_accounts(row[0]) if row else []
        accounts.append(account)
        seen.add(key)
    for row in LOL_ROSTER:
        if len(accounts) >= max(TRACKED_LOL_PLAYERS, len(saved)):
            break
        if row[0].casefold() not in seen:
            accounts.append({**roster_entry(*row), "alts": alt_accounts(row[0])})
            seen.add(row[0].casefold())
    return accounts
