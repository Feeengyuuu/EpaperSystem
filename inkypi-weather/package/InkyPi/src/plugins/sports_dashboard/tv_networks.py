"""Broadcaster names to the logos bundled in assets/logos/tv.

ESPN, MLB statsapi and the club feeds name networks in their own shorthand
("NFL Net", "Fox", "CBSSN", "Golf Chnl") and join several with a slash
("ESPN/ABC/Disney+"). ``tv_network_parts`` splits such a label and gives each
part its logo key, or None when no logo is bundled for it.
"""

from __future__ import annotations

import re

TV_NETWORK_ALIASES = {
    "espn": ("espn",),
    "espn2": ("espn2",),
    "espnu": ("espnu",),
    "espn_plus": ("espnplus",),
    "espn_deportes": ("espndeportes",),
    "abc": ("abc",),
    "cbs": ("cbs",),
    "cbssn": ("cbssn", "cbssportsnetwork", "cbssportsnet"),
    "fox": ("fox",),
    "fs1": ("fs1", "foxsports1"),
    "nbc": ("nbc",),
    "peacock": ("peacock",),
    "usa": ("usa", "usanet", "usanetwork"),
    "cnbc": ("cnbc",),
    "cw": ("cw", "thecw", "cwnetwork"),
    "tnt": ("tnt",),
    "tbs": ("tbs",),
    "trutv": ("trutv",),
    "hbo_max": ("hbomax", "max"),
    "prime_video": ("primevideo", "amazonprimevideo", "prime", "amazon"),
    "netflix": ("netflix",),
    "nfl_network": ("nflnet", "nflnetwork", "nfln"),
    "nba_tv": ("nbatv",),
    "mlb_network": ("mlbnet", "mlbnetwork", "mlbn"),
    "apple_tv": ("appletv", "appletvplus"),
    "paramount_plus": ("paramountplus",),
    "sec_network": ("secnetwork", "secn"),
    "acc_network": ("accnetwork", "accn"),
    "btn": ("btn", "bigtennetwork"),
    "golf_channel": ("golf", "golfchnl", "golfchannel"),
    "bein": ("bein", "beinsports", "beinsportsusa"),
    "dazn": ("dazn",),
    "disney_plus": ("disneyplus",),
    "universo": ("universo",),
    "fandango": ("fandango", "fandangoathome"),
    "nbcsn": ("nbcsn", "nbcsportsnetwork"),
    "nhl_network": ("nhlnet", "nhlnetwork", "nhln"),
    "hulu": ("hulu",),
    "ion": ("ion", "iontv", "iontelevision"),
    "youtube": ("youtube",),
    # The conferences' streams, as ESPN writes them ("SECN+", "SEC Network +", "B1G+", "ACCNX", "MW+").
    "sec_network_plus": ("secnplus", "secnetworkplus"),
    "b1g_plus": ("b1gplus", "bigtenplus"),
    "accnx": ("accnx", "accnetworkextra"),
    "mw_plus": ("mwplus",),
    # Canada's, for the MLS's Canadian clubs and the NHL: each network's channels read as its logo.
    "tva_sports": ("tvasports", "tvas", "tvasports2", "tvas2"),
    "tsn": ("tsn", "tsn1", "tsn2", "tsn3", "tsn4", "tsn5"),
    "sportsnet": ("sportsnet", "sn", "sn1", "snnow", "sportsnetnow", "sportsnetone", "sportsnet360", "sn360",
                  "sne", "snw", "sno", "snp", "sportsneteast", "sportsnetwest", "sportsnetontario", "sportsnetpacific"),
    "rds": ("rds", "rds2", "rdsinfo"),
}
TV_NETWORK_KEYS = {alias: key for key, aliases in TV_NETWORK_ALIASES.items() for alias in aliases}
# Where a viewer in the US most likely watches, first: the streaming services, then the other
# networks as the feed lists them, the Spanish-language feeds last. A row with room for one logo
# shows the first.
TV_STREAMING_KEYS = ("espn_plus", "peacock", "paramount_plus", "prime_video", "apple_tv", "netflix", "hbo_max",
                     "disney_plus", "hulu", "dazn", "fandango", "youtube", "sec_network_plus", "b1g_plus", "accnx",
                     "mw_plus")
TV_SPANISH_KEYS = ("espn_deportes", "universo")
# A logo may be up to four times as wide as it is tall; a network's set on one long line (TVA Sports' two
# lines put side by side, Sportsnet's and NBCSN's wordmarks) up to this many times, so it stays readable.
TV_LOGO_WIDTH_RATIOS = {"tva_sports": 10, "sportsnet": 7, "nbcsn": 6}


def tv_logo_width_ratio(key):
    """How many times as wide as tall the network's logo may be drawn."""
    return TV_LOGO_WIDTH_RATIOS.get(key, 4)


def tv_watch_rank(key):
    """Sort rank of a network's logo key by where a US viewer would watch it."""
    if key in TV_STREAMING_KEYS:
        return TV_STREAMING_KEYS.index(key)
    return 99 if key in TV_SPANISH_KEYS else 50


def tv_network_key(name):
    """The logo key for one network name, or None."""
    normalized = re.sub(r"[^a-z0-9]", "", str(name or "").lower().replace("+", "plus"))
    return TV_NETWORK_KEYS.get(normalized)


def tv_network_parts(label):
    """``[(name, key or None), ...]`` for each network in a broadcast label."""
    names = [part.strip() for part in re.split(r"[/,|]", str(label or "")) if part.strip()]
    return [(name, tv_network_key(name)) for name in names]
