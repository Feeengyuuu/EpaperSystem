"""今日人物与节日: who was born and who died on this date, and the day's holidays, picked
from the Chinese Wikipedia date page the plugin already downloads for its 大事记.

The page lists everyone (eighty births on a busy day), so each list is ranked: a Nobel
laureate, a head of state or a scientist, writer or artist outranks an idol-group member,
a round anniversary this year (100, 50, 25 years) lifts an entry, and a Chinese one counts a
little more; the best known come first. Holidays keep the page's order, short names first.
"""

from __future__ import annotations

import re

# Each list keeps this many of its best entries; the page shows whole entries while they fit.
KEEP = {"births": 4, "deaths": 4, "holidays": 6}
# A holiday name longer than this goes after the short ones.
LONG_HOLIDAY = 8
# The date page's section ids: Wikipedia keeps the traditional spelling in an id even on a
# zh-cn rendering, so both spellings are matched.
SECTION_IDS = {
    "births": r"出生",
    "deaths": r"逝世",
    "holidays": r"[节節]假日和[习習]俗",
}
# What a person was, as the page says it: the better known kinds of lives first.
ROLE_WEIGHTS = (
    ("诺贝尔", 6), ("总统", 5), ("皇帝", 5), ("国王", 4), ("女王", 4), ("总理", 4), ("首相", 4),
    ("皇后", 4), ("王后", 3), ("苏丹", 3), ("哲学家", 4), ("数学家", 4), ("物理学家", 4), ("化学家", 4),
    ("天文学家", 4), ("戏剧家", 4), ("作曲家", 4), ("生物学家", 3), ("科学家", 3), ("经济学家", 3),
    ("社会学家", 3), ("历史学家", 3), ("思想家", 3), ("发明家", 3), ("建筑师", 3), ("作家", 3),
    ("小说家", 3), ("诗人", 3), ("剧作家", 3), ("画家", 3), ("音乐家", 3), ("指挥家", 3), ("导演", 3),
    ("美学家", 3), ("探险家", 2), ("教育家", 2), ("将领", 2), ("政治人物", 2), ("运动员", 1),
    ("演员", 1), ("歌手", 1),
)
# Lives the page lists in numbers but few readers would know.
MINOR_ROLES = ("偶像团体", "声优", "配音员", "艺人", "模特", "主持人", "超模", "退伍军人")
_PERSON = re.compile(r"^\s*(\d{1,4})年\s*[：:]\s*(.+)$")
_OTHER_YEAR = re.compile(r"[（(]\s*(\d{1,4})年(?:出生|逝世)\s*[）)]")
_CITATION = re.compile(r"\[\d+\]")


def section_html(html_text, key):
    """The HTML between the ``key`` section's heading and the next heading, or ""."""
    start = re.search(r'id=["\']' + SECTION_IDS[key] + r'["\']', html_text)
    if not start:
        return ""
    rest = html_text[start.end():]
    following = re.search(r"<h[1-3]\b", rest)
    return rest[:following.start()] if following else rest


def list_items(section, html_text_of):
    """The section's list items as plain text (citation marks dropped)."""
    items = []
    for item_html in re.findall(r"<li\b[^>]*>(.*?)</li>", section, flags=re.IGNORECASE | re.DOTALL):
        text = _CITATION.sub("", html_text_of(item_html)).strip()
        if text:
            items.append(text)
    return items


def parse_person(text):
    """"1815年：乔治·布尔，英国数学家、哲学家（1864年逝世）" -> its year, name, role and other year."""
    match = _PERSON.match(text)
    if not match:
        return None
    rest = match.group(2)
    other = _OTHER_YEAR.search(rest)
    rest = _OTHER_YEAR.sub("", rest).strip(" ，,")
    name, _sep, role = rest.partition("，")
    name, role = name.strip(), role.strip(" ，,")
    if not name or len(name) > 24:
        return None
    return {"year": match.group(1), "name": name, "role": role, "other_year": other.group(1) if other else ""}


def parse_holiday(text):
    """"墨西哥、 巴西：亡灵节" -> {"name": "亡灵节", "where": "墨西哥、巴西"}."""
    text = " ".join(text.split())
    where, sep, name = text.rpartition("：") if "：" in text else text.rpartition(":")
    if not sep:
        where, name = "", text
    name, where = name.strip(), re.sub(r"\s*、\s*", "、", where).strip()
    return {"name": name, "where": where} if name else None


def person_score(person, year):
    role = person.get("role") or ""
    # What they were is the role's first clause, after any "的" ("…总统吴廷琰的胞弟" was not a
    # president); a Nobel prize counts wherever the role names it.
    head = re.split(r"[，,；;]", role)[0].rsplit("的", 1)[-1]
    score = max((weight for word, weight in ROLE_WEIGHTS if word in head or word == "诺贝尔" and word in role),
                default=0)
    if any(word in role for word in MINOR_ROLES):
        score -= 2
    if "中国" in role:
        score += 1
    try:
        since = int(year) - int(person.get("year"))
    except (TypeError, ValueError):
        since = 0
    if since > 0:
        score += 3 if since % 100 == 0 else 2 if since % 50 == 0 else 1 if since % 25 == 0 else 0
    return score


def ranked_people(people, year, keep):
    """The ``keep`` best known of ``people``, best first (the earlier on the page on a tie)."""
    scored = sorted(enumerate(people), key=lambda pair: (-person_score(pair[1], year), pair[0]))
    return [person for _index, person in scored[:keep]]


def short_role(role, limit=10):
    """The first thing a role names ("英国数学家、哲学家" -> "英国数学家"), "" when it runs long."""
    first = re.split(r"[、，,；;（(]", str(role or ""))[0].strip()
    return first if 0 < len(first) <= limit else ""


def almanac_from_html(html_text, year, html_text_of):
    """{"births": [...], "deaths": [...], "holidays": [...]} from a zh-cn date page."""
    people = {}
    for key in ("births", "deaths"):
        parsed = [parse_person(text) for text in list_items(section_html(html_text, key), html_text_of)]
        people[key] = ranked_people([person for person in parsed if person], year, KEEP[key])
    holidays = [parse_holiday(text) for text in list_items(section_html(html_text, "holidays"), html_text_of)]
    holidays = [holiday for holiday in holidays if holiday]
    # A holiday known by a short name leads; a long official one ("终止针对记者犯罪不受惩罚
    # 现象国际日") follows, shown when the line still has room.
    holidays.sort(key=lambda holiday: len(holiday["name"]) > LONG_HOLIDAY)
    return {**people, "holidays": holidays[:KEEP["holidays"]]}
