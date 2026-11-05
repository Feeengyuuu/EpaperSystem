"""Where each of the day's holidays belongs, so the almanac line can lead it with that place's
flag and name: "[flag] 英国 篝火之夜", "[UN flag] 联合国 世界海啸日".

The date page states most places itself ("巴拿马：科隆日", the place led by its flag icon); those
names map to flagcdn's codes here (the project's flag source, true proportions), and a place
this table does not know keeps the Wikimedia Commons flag the page shows for it. A holiday the
page gives no place is a United Nations day when the UN's own list has it on that date. Nothing
here touches the network; the plugin asks Wikidata only about what is still unplaced.
"""

from __future__ import annotations

from functools import lru_cache
import json
from pathlib import Path
import re

UN_PLACE = {"name": "联合国", "flag": "un"}
UN_DAYS_PATH = Path(__file__).resolve().parent / "assets" / "un_days.json"
# A holiday line shows at most this many places' flags and names; more end in "等".
MAX_PLACES = 2
# The flag pictures a page may name: Wikimedia's own hosts only.
COMMONS_FLAG_URL = re.compile(r"^https://(?:upload|thumb)\.wikimedia\.org/wikipedia/commons/[\w./%()~,+-]+\.png$")
# Places as the zh-cn date pages name them -> flagcdn codes (ISO 3166-1 alpha-2, the UK's four
# nations as flagcdn's gb-*, the EU); "un" is the UN flag bundled from Wikimedia Commons.
FLAG_CODES = {
    # Asia
    "中国": "cn", "中华人民共和国": "cn", "香港": "hk", "澳门": "mo", "台湾": "tw", "中华民国": "tw",
    "日本": "jp", "韩国": "kr", "大韩民国": "kr", "朝鲜": "kp", "蒙古": "mn", "蒙古国": "mn",
    "越南": "vn", "老挝": "la", "柬埔寨": "kh", "泰国": "th", "缅甸": "mm", "马来西亚": "my",
    "新加坡": "sg", "印度尼西亚": "id", "印尼": "id", "文莱": "bn", "菲律宾": "ph", "东帝汶": "tl",
    "印度": "in", "巴基斯坦": "pk", "孟加拉国": "bd", "孟加拉": "bd", "斯里兰卡": "lk", "尼泊尔": "np",
    "不丹": "bt", "马尔代夫": "mv", "阿富汗": "af", "伊朗": "ir", "伊拉克": "iq", "叙利亚": "sy",
    "黎巴嫩": "lb", "以色列": "il", "巴勒斯坦": "ps", "约旦": "jo", "沙特阿拉伯": "sa", "沙特": "sa",
    "也门": "ye", "阿曼": "om", "阿联酋": "ae", "阿拉伯联合酋长国": "ae", "卡塔尔": "qa", "巴林": "bh",
    "科威特": "kw", "土耳其": "tr", "塞浦路斯": "cy", "格鲁吉亚": "ge", "亚美尼亚": "am",
    "阿塞拜疆": "az", "哈萨克斯坦": "kz", "乌兹别克斯坦": "uz", "土库曼斯坦": "tm",
    "吉尔吉斯斯坦": "kg", "塔吉克斯坦": "tj",
    # Europe
    "英国": "gb", "大不列颠及北爱尔兰联合王国": "gb", "英格兰": "gb-eng", "苏格兰": "gb-sct",
    "威尔士": "gb-wls", "北爱尔兰": "gb-nir", "爱尔兰": "ie", "法国": "fr", "德国": "de", "荷兰": "nl",
    "比利时": "be", "卢森堡": "lu", "瑞士": "ch", "奥地利": "at", "列支敦士登": "li", "意大利": "it",
    "圣马力诺": "sm", "梵蒂冈": "va", "马耳他": "mt", "西班牙": "es", "葡萄牙": "pt", "安道尔": "ad",
    "摩纳哥": "mc", "丹麦": "dk", "挪威": "no", "瑞典": "se", "芬兰": "fi", "冰岛": "is",
    "爱沙尼亚": "ee", "拉脱维亚": "lv", "立陶宛": "lt", "波兰": "pl", "捷克": "cz", "捷克共和国": "cz",
    "斯洛伐克": "sk", "匈牙利": "hu", "斯洛文尼亚": "si", "克罗地亚": "hr", "波黑": "ba",
    "波斯尼亚和黑塞哥维那": "ba", "塞尔维亚": "rs", "黑山": "me", "科索沃": "xk", "北马其顿": "mk", "阿尔巴尼亚": "al",
    "希腊": "gr", "保加利亚": "bg", "罗马尼亚": "ro", "摩尔多瓦": "md", "乌克兰": "ua", "白俄罗斯": "by",
    "俄罗斯": "ru", "俄罗斯联邦": "ru", "法罗群岛": "fo", "直布罗陀": "gi", "马恩岛": "im",
    "泽西": "je", "根西": "gg",
    # Africa
    "埃及": "eg", "利比亚": "ly", "突尼斯": "tn", "阿尔及利亚": "dz", "摩洛哥": "ma",
    "毛里塔尼亚": "mr", "马里": "ml", "尼日尔": "ne", "乍得": "td", "苏丹": "sd", "南苏丹": "ss",
    "埃塞俄比亚": "et", "厄立特里亚": "er", "吉布提": "dj", "索马里": "so", "肯尼亚": "ke",
    "乌干达": "ug", "卢旺达": "rw", "布隆迪": "bi", "坦桑尼亚": "tz", "莫桑比克": "mz", "马拉维": "mw",
    "赞比亚": "zm", "津巴布韦": "zw", "博茨瓦纳": "bw", "纳米比亚": "na", "南非": "za", "莱索托": "ls",
    "斯威士兰": "sz", "埃斯瓦蒂尼": "sz", "马达加斯加": "mg", "毛里求斯": "mu", "塞舌尔": "sc",
    "科摩罗": "km", "安哥拉": "ao", "刚果民主共和国": "cd", "刚果（金）": "cd", "刚果共和国": "cg",
    "刚果（布）": "cg", "加蓬": "ga", "赤道几内亚": "gq", "圣多美和普林西比": "st", "喀麦隆": "cm",
    "中非共和国": "cf", "中非": "cf", "尼日利亚": "ng", "贝宁": "bj", "多哥": "tg", "加纳": "gh",
    "科特迪瓦": "ci", "利比里亚": "lr", "塞拉利昂": "sl", "几内亚": "gn", "几内亚比绍": "gw",
    "塞内加尔": "sn", "冈比亚": "gm", "佛得角": "cv", "布基纳法索": "bf", "西撒哈拉": "eh",
    "留尼汪": "re", "马约特": "yt",
    # The Americas
    "美国": "us", "美利坚合众国": "us", "加拿大": "ca", "墨西哥": "mx", "危地马拉": "gt", "伯利兹": "bz",
    "萨尔瓦多": "sv", "洪都拉斯": "hn", "尼加拉瓜": "ni", "哥斯达黎加": "cr", "巴拿马": "pa", "古巴": "cu",
    "牙买加": "jm", "海地": "ht", "多米尼加": "do", "多米尼加共和国": "do", "多米尼克": "dm",
    "巴哈马": "bs", "巴巴多斯": "bb", "特立尼达和多巴哥": "tt", "格林纳达": "gd", "圣卢西亚": "lc",
    "圣文森特和格林纳丁斯": "vc", "安提瓜和巴布达": "ag", "圣基茨和尼维斯": "kn", "哥伦比亚": "co",
    "委内瑞拉": "ve", "圭亚那": "gy", "苏里南": "sr", "厄瓜多尔": "ec", "秘鲁": "pe", "玻利维亚": "bo",
    "巴西": "br", "巴拉圭": "py", "乌拉圭": "uy", "阿根廷": "ar", "智利": "cl", "波多黎各": "pr",
    "格陵兰": "gl", "百慕大": "bm", "开曼群岛": "ky", "阿鲁巴": "aw", "库拉索": "cw",
    "美属维尔京群岛": "vi", "英属维尔京群岛": "vg", "法属圭亚那": "gf", "瓜德罗普": "gp",
    "马提尼克": "mq", "福克兰群岛": "fk",
    # Oceania
    "澳大利亚": "au", "新西兰": "nz", "巴布亚新几内亚": "pg", "斐济": "fj", "所罗门群岛": "sb",
    "瓦努阿图": "vu", "萨摩亚": "ws", "汤加": "to", "基里巴斯": "ki", "图瓦卢": "tv", "瑙鲁": "nr",
    "帕劳": "pw", "密克罗尼西亚联邦": "fm", "马绍尔群岛": "mh", "库克群岛": "ck", "纽埃": "nu",
    "关岛": "gu", "美属萨摩亚": "as", "北马里亚纳群岛": "mp", "新喀里多尼亚": "nc", "法属波利尼西亚": "pf",
    # Unions
    "欧盟": "eu", "欧洲联盟": "eu", "联合国": "un",
}
# A place the table names only by its country ("美国阿拉斯加州") still takes that country's flag
# when what follows is a subdivision word.
SUBDIVISION_ENDS = ("州", "省", "县", "市", "地区", "邦", "北部", "南部", "东部", "西部")
FLAG_KEY = re.compile(r"^(?:[a-z]{2}|gb-(?:eng|sct|wls|nir))$")
# Words every UN day shares, dropped before comparing what a day is about.
_GENERIC = re.compile(r"\[[^\]]*\]|［[^］]*］|[，,].*$|\s+|联合国|世界|国际|全球|纪念日|宣传日|意识日|纪念|意识|宣传|"
                      r"提高|认识|节|日|周|的")


@lru_cache(maxsize=1)
def un_days():
    """{"MM-DD": [the UN's official Chinese names of its days on that date]}; {} when unreadable."""
    try:
        days = json.loads(UN_DAYS_PATH.read_text(encoding="utf-8")).get("days")
    except (OSError, ValueError, AttributeError):
        return {}
    return days if isinstance(days, dict) else {}


def _core(name):
    return _GENERIC.sub("", str(name or ""))


def is_un_day(name, month, day):
    """Whether ``name`` is one of the UN's days on that date ("世界海啸日" is its 世界海啸意识日)."""
    core = _core(name)
    if len(core) < 2:
        return False
    for official in un_days().get(f"{int(month):02d}-{int(day):02d}", []):
        other = _core(official)
        if len(other) < 2:
            continue
        if core in other or other in core:
            return True
        shared = sum(min(core.count(char), other.count(char)) for char in set(core))
        if shared >= 0.75 * min(len(core), len(other)):
            return True
    return False


def flag_code(place):
    """flagcdn's code for a place as the page names it, or ""."""
    return FLAG_CODES.get(place, "")


def place_name(code):
    """The short name the table gives a flag code first ("gb" -> "英国"), or ""."""
    return next((name for name, value in FLAG_CODES.items() if value == code), "")


def _subdivision_code(place):
    for name in sorted(FLAG_CODES, key=len, reverse=True):
        rest = place[len(name):]
        if place.startswith(name) and rest and rest.endswith(SUBDIVISION_ENDS):
            return FLAG_CODES[name]
    return ""


def place_parts(where):
    """"墨西哥、 巴西" or "德国 · 荷兰" -> the places; a bracketed note ("美国（部分州）") is dropped."""
    where = re.sub(r"[（(][^（）()]*[）)]", "", str(where or ""))
    return [part.strip() for part in re.split(r"[、，,/·‧・]", where) if part.strip()]


def stated_places(where, flag_images=()):
    """The places a holiday's stated ``where`` names, each with a flagcdn code ("flag") or the
    page's own Commons flag ("flag_url"); [] unless every part has one of them."""
    parts = place_parts(where)
    if not parts:
        return []
    if parts[0].startswith("联合国"):
        return [dict(UN_PLACE)]
    images = list(flag_images) if len(flag_images) == len(parts) else []
    places = []
    for index, part in enumerate(parts):
        # The table's own name first, then the flag the page shows for it, then its country's.
        image = images[index] if images else ""
        if not flag_code(part) and image and COMMONS_FLAG_URL.match(image):
            places.append({"name": part, "flag_url": image})
        elif code := flag_code(part) or _subdivision_code(part):
            places.append({"name": part, "flag": code})
        else:
            return []
    return places


def place_holidays(holidays, month, day):
    """Each holiday with its ``places`` where the page states them, or the UN's when the UN
    lists it that day; the rest are returned as they are (still carrying their ``article``)."""
    placed = []
    for holiday in holidays:
        holiday = dict(holiday)
        places = stated_places(holiday.get("where"), holiday.get("flag_images") or ())
        if not places and not holiday.get("where") and is_un_day(holiday.get("name"), month, day):
            places = [dict(UN_PLACE)]
        if places:
            holiday["places"] = places
        placed.append(holiday)
    return placed


def public_holiday(holiday):
    """A holiday as the payload keeps it: name, where and (when known) places."""
    return {key: holiday[key] for key in ("name", "where", "places") if key in holiday}
