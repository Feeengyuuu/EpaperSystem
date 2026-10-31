"""A Steam game's studio logo: the avatar of the creator clan its store entry names as developer.

Steam's store (IStoreBrowseService/GetItems, basic info) gives each developer's creator clan; the
clan's public member-list page gives its avatar, which studios set to their logo.
"""

from __future__ import annotations

import re

STORE_ITEMS_URL = "https://api.steampowered.com/IStoreBrowseService/GetItems/v1/"
CLAN_XML_URL = "https://steamcommunity.com/gid/{gid}/memberslistxml/?xml=1"
CLAN_STEAMID_BASE = 103582791429521408
AVATAR_RE = re.compile(
    r"<avatarFull>\s*(?:<!\[CDATA\[)?\s*(https://avatars\.(?:[a-z]+\.)?steamstatic\.com/[0-9a-f]{40}_full\.jpg)"
)


def clan_page_url(clan):
    """The public page of a creator clan (by its account id) that carries its avatar."""
    return CLAN_XML_URL.format(gid=CLAN_STEAMID_BASE + int(clan))


def avatar_url(page):
    """The clan's full-size avatar named on its page, or ""."""
    match = AVATAR_RE.search(page or "")
    return match.group(1) if match else ""


def developer_clans(store_items):
    """Each app's first developer's creator clan (account id, as text) from GetItems' items; apps
    whose developer has none are left out."""
    clans = {}
    for item in store_items or ():
        if not isinstance(item, dict) or not str(item.get("appid") or "").isdecimal():
            continue
        basic = item.get("basic_info") if isinstance(item.get("basic_info"), dict) else {}
        developers = [entry for entry in basic.get("developers") or [] if isinstance(entry, dict)]
        clan = str((developers[0].get("creator_clan_account_id") if developers else "") or "")
        if clan.isdecimal() and int(clan):
            clans[str(item["appid"])] = clan
    return clans
