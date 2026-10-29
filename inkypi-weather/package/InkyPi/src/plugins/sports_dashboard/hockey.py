"""The NHL on the sports page's lower panel.

ESPN's hockey scoreboard answers one day at a time (a date range is refused), so the hub reads
yesterday to two days ahead and merges them. Each game is parsed like the NBA's ESPN games (scores
by period, clock, venue, TV, records) and then given the teams' Chinese names and their goals /
assists / points leaders in place of basketball's points / rebounds / assists.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import timedelta

DEFAULT_NHL_SCOREBOARD_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
# The days around today the hub reads (each a request): yesterday's finals to two days ahead.
NHL_DAY_OFFSETS = (-1, 0, 1, 2)
NHL_TEAM_ZH_NAMES = {
    "ANA": "鸭", "BOS": "棕熊", "BUF": "军刀", "CGY": "火焰", "CAR": "飓风", "CHI": "黑鹰", "COL": "雪崩",
    "CBJ": "蓝衣", "DAL": "星", "DET": "红翼", "EDM": "油人", "FLA": "美洲豹", "LA": "国王", "MIN": "荒野",
    "MTL": "加拿大人", "NSH": "掠夺者", "NJ": "魔鬼", "NYI": "岛人", "NYR": "游骑兵", "OTT": "参议员",
    "PHI": "飞人", "PIT": "企鹅", "SJ": "鲨鱼", "SEA": "海妖", "STL": "蓝调", "TB": "闪电", "TOR": "枫叶",
    "UTAH": "猛犸", "VAN": "加人", "VGK": "金骑士", "WSH": "首都人", "WPG": "喷气机",
}
NHL_TEAM_ZH_FULL_NAMES = {
    "ANA": "阿纳海姆鸭", "BOS": "波士顿棕熊", "BUF": "布法罗军刀", "CGY": "卡尔加里火焰", "CAR": "卡罗来纳飓风",
    "CHI": "芝加哥黑鹰", "COL": "科罗拉多雪崩", "CBJ": "哥伦布蓝衣", "DAL": "达拉斯星", "DET": "底特律红翼",
    "EDM": "埃德蒙顿油人", "FLA": "佛罗里达美洲豹", "LA": "洛杉矶国王", "MIN": "明尼苏达荒野",
    "MTL": "蒙特利尔加拿大人", "NSH": "纳什维尔掠夺者", "NJ": "新泽西魔鬼", "NYI": "纽约岛人", "NYR": "纽约游骑兵",
    "OTT": "渥太华参议员", "PHI": "费城飞人", "PIT": "匹兹堡企鹅", "SJ": "圣何塞鲨鱼", "SEA": "西雅图海妖",
    "STL": "圣路易斯蓝调", "TB": "坦帕湾闪电", "TOR": "多伦多枫叶", "UTAH": "犹他猛犸", "VAN": "温哥华加人",
    "VGK": "维加斯金骑士", "WSH": "华盛顿首都人", "WPG": "温尼伯喷气机",
}
# ESPN's other spellings of a few codes.
NHL_CODE_ALIASES = {"LAK": "LA", "SJS": "SJ", "TBL": "TB", "NJD": "NJ", "UTA": "UTAH", "WAS": "WSH"}
LEADER_CATEGORIES = (("goals", "G"), ("assists", "A"), ("points", "PTS"))
LEADER_LABELS = tuple(label for _category, label in LEADER_CATEGORIES)
REGULATION_PERIODS = 3


def scoreboard_url(settings):
    return str((settings or {}).get("nhlScoreboardUrl") or DEFAULT_NHL_SCOREBOARD_URL).strip() or DEFAULT_NHL_SCOREBOARD_URL


def scoreboard_days(local_date):
    """The YYYYMMDD days the hub reads, oldest first."""
    return [(local_date + timedelta(days=offset)).strftime("%Y%m%d") for offset in NHL_DAY_OFFSETS]


def merge_scoreboards(boards):
    """The days' scoreboards as one, each game once."""
    events, seen = [], set()
    for board in boards:
        for event in (board or {}).get("events") or []:
            key = str((event or {}).get("id") or "")
            if isinstance(event, Mapping) and key not in seen:
                seen.add(key)
                events.append(event)
    return {"events": events}


def team_code(code):
    value = str(code or "").strip().upper()
    return NHL_CODE_ALIASES.get(value, value)


def display_team(event, side, full=False):
    """A side's Chinese name ("鲨鱼", or in full "圣何塞鲨鱼"), else ESPN's own."""
    event = event or {}
    code = team_code(event.get(f"team_{side}_code"))
    names = NHL_TEAM_ZH_FULL_NAMES if full else NHL_TEAM_ZH_NAMES
    return names.get(code) or str(event.get(f"team_{side}") or event.get(f"team_{side}_name") or code or "TBD")


def team_leaders(competitor):
    """This team's goals / assists / points leaders: [{label, name, value}]."""
    groups = {group.get("name"): group for group in (competitor or {}).get("leaders") or [] if isinstance(group, Mapping)}
    leaders = []
    for category, label in LEADER_CATEGORIES:
        top = (((groups.get(category) or {}).get("leaders") or [None])[0]) or {}
        name = str((top.get("athlete") or {}).get("shortName") or "").strip()
        if name:
            leaders.append({"label": label, "name": name, "value": str(top.get("displayValue") or "").strip()})
    return leaders


def period_label(period):
    """1-3 -> "P1".."P3", then "OT", "2OT" …; a shootout (ESPN's period 5 in the regular season) "SO"."""
    period = int(period or 0)
    if period <= 0:
        return ""
    if period <= REGULATION_PERIODS:
        return f"P{period}"
    return "OT" if period == REGULATION_PERIODS + 1 else f"{period - REGULATION_PERIODS}OT"


def parse_scoreboard(dashboard, payload, timezone_info):
    """The merged scoreboard's games, parsed by the NBA's ESPN reader and then made hockey's."""
    events = dashboard._parse_nba_espn_events(payload, timezone_info)
    sides = {}
    for raw in (payload or {}).get("events") or []:
        competition = (((raw or {}).get("competitions") or [{}])[0]) or {}
        away, home = dashboard._nba_competitors_by_side(competition.get("competitors") or [])
        sides[str((raw or {}).get("id") or "")] = (away, home)
    for event in events:
        event["sport"] = "NHL"
        event["block"] = "NHL"
        away, home = sides.get(str(event.get("event_id") or ""), (None, None))
        for side, competitor in (("a", away), ("b", home)):
            prefix = f"team_{side}"
            event[f"{prefix}_code"] = team_code(event.get(f"{prefix}_code"))
            event[prefix] = display_team(event, side)
            started = event.get("state") != "unstarted"
            event[f"team_leaders_{side}"] = team_leaders(competitor) if competitor and started else []
        event["leaders"] = []
    return {"events": events}
