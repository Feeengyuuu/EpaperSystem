"""Each tour-level tennis tournament's own official mark and venue, found from ESPN's name and place.

The marks are bundled (assets/logos/tennis_event_<key>.png, 64 px tall, recorded in
logos.source.json): the ATP Tour's tournament logos from each event's overview page on atptour.com
(2026 calendar, gathered 2026-11-09), and the FAGE WTA Finals Indian Wells logo from wtatennis.com.
The ATP serves most of its logos white for dark heroes; those are stored in one dark ink, so the day
page prints them and night_inked lightens them by night. A logo printed on its own dark box keeps its
colours both ways ("boxed"). WTA events that share their brand with the ATP event (Indian Wells,
Miami, Madrid, Rome, Cincinnati, Beijing ...) carry the same mark; the WTA publishes no logo of its
own events, so the rest keep the WTA's tier tag. The venue names the hall or club where it is well
known; without one the card names the city ESPN gives.

Rows are matched in order on the lower-cased tournament name and ESPN's place line, only for the
tours a row names; the first match wins. Re-curate when a sponsor or logo changes.
"""

from __future__ import annotations

import re

ATP, WTA, BOTH = ("ATP",), ("WTA",), ("ATP", "WTA")

# (key, tours, pattern, venue, mark): mark is "mono" (stored in dark ink), "colour", "boxed" or None
# (no bundled logo: the venue alone).
EVENTS = (
    # Finals and team events first: their names hold host cities that other rows match.
    ("wta_finals", WTA, r"wta finals.*indian wells|fage wta finals", "", "colour"),
    ("next_gen_atp_finals", ATP, r"next gen", "", "mono"),
    ("nitto_atp_finals", ATP, r"atp finals", "", "mono"),
    ("davis_cup_finals", ATP, r"davis cup", "", "mono"),
    ("laver_cup", ATP, r"laver cup", "", "colour"),
    # The Grand Slams keep their own bundled crests (tennis.MARKS); these name their grounds.
    ("australian_open", BOTH, r"australian open", "Melbourne Park", None),
    ("roland_garros", BOTH, r"roland.?garros|french open", "Stade Roland-Garros", None),
    ("wimbledon", BOTH, r"wimbledon", "All England Club", None),
    ("us_open", BOTH, r"\bus open\b", "Billie Jean King Tennis Center", None),
    # ATP calendar order; BOTH where the WTA event shares the brand (and so the logo).
    ("brisbane", BOTH, r"brisbane", "Queensland Tennis Centre", "mono"),
    ("hong_kong", ATP, r"hong kong", "Victoria Park", "mono"),
    ("adelaide", BOTH, r"adelaide", "Memorial Drive", "colour"),
    ("auckland", BOTH, r"auckland|asb classic", "ASB Tennis Arena", "boxed"),
    ("montpellier", ATP, r"montpellier|occitanie", "Sud de France Arena", "mono"),
    ("dallas", ATP, r"dallas", "Ford Center at The Star", "mono"),
    ("rotterdam", ATP, r"rotterdam|abn amro", "Rotterdam Ahoy", "colour"),
    ("buenos_aires", ATP, r"buenos aires|argentina open", "Buenos Aires Lawn Tennis", "mono"),
    ("doha", ATP, r"doha|qatar", "Khalifa Tennis Complex", "mono"),
    ("rio_de_janeiro", ATP, r"rio de janeiro|rio open", "Jockey Club Brasileiro", "mono"),
    ("delray_beach", ATP, r"delray", "Delray Beach Tennis Center", "mono"),
    ("dubai", BOTH, r"dubai", "Dubai Tennis Stadium", "mono"),
    ("acapulco", ATP, r"acapulco|abierto mexicano", "Arena GNP Seguros", "mono"),
    ("santiago", ATP, r"santiago|chile open", "San Carlos de Apoquindo", "mono"),
    ("indian_wells", BOTH, r"indian wells|bnp paribas open", "Indian Wells Tennis Garden", "mono"),
    ("miami", BOTH, r"miami", "Hard Rock Stadium", "mono"),
    ("houston", ATP, r"houston|clay court championship", "River Oaks Country Club", "mono"),
    ("marrakech", ATP, r"marrakech|hassan ii", "Royal Tennis Club", "mono"),
    ("bucharest", ATP, r"bucharest|tiriac", "Arenele BNR", "colour"),
    ("monte_carlo", ATP, r"monte.?carlo", "Monte-Carlo Country Club", "mono"),
    ("barcelona", ATP, r"barcelona", "RCT Barcelona", "mono"),
    ("munich", ATP, r"munich|bmw open", "MTTC Iphitos", "mono"),
    ("madrid", BOTH, r"madrid", "Caja Mágica", "mono"),
    ("rome", BOTH, r"\brome\b|internazionali|d'italia", "Foro Italico", "mono"),
    ("hamburg", ATP, r"hamburg", "Am Rothenbaum", "boxed"),
    ("geneva", ATP, r"geneva|gen[eè]ve", "Tennis Club de Genève", "mono"),
    ("s_hertogenbosch", BOTH, r"hertogenbosch|lib[eé]ma", "Autotron Rosmalen", "mono"),
    ("london", BOTH, r"queen'?s club|hsbc championships", "Queen's Club", "mono"),
    ("halle", ATP, r"\bhalle\b|terra wortmann", "OWL Arena", "colour"),
    ("mallorca", ATP, r"mallorca", "Mallorca Country Club", "mono"),
    ("eastbourne", BOTH, r"eastbourne", "Devonshire Park", "mono"),
    ("gstaad", ATP, r"gstaad", "Roy Emerson Arena", "mono"),
    ("bastad", ATP, r"b[aå]stad|nordea", "Båstad Tennis Stadium", "colour"),
    ("umag", ATP, r"umag|croatia open", "", "mono"),
    ("kitzbuhel", ATP, r"kitzb[uü]hel|generali open", "Sportpark Kitzbühel", "colour"),
    ("estoril", ATP, r"estoril", "Clube de Ténis do Estoril", "mono"),
    ("washington", BOTH, r"washington|dc open", "Rock Creek Park", "colour"),
    ("los_cabos", ATP, r"los cabos|mifel", "Cabo Sports Complex", "mono"),
    ("montreal", ATP, r"montr[eé]al|banque nationale", "IGA Stadium", "mono"),
    ("cincinnati", BOTH, r"cincinnati", "Lindner Family Tennis Center", "mono"),
    ("winston_salem", ATP, r"winston", "Wake Forest Tennis Complex", "mono"),
    ("chengdu", ATP, r"chengdu", "Sichuan Tennis Center", "mono"),
    ("hangzhou", ATP, r"hangzhou", "", "mono"),
    ("beijing", BOTH, r"beijing|china open", "National Tennis Center", "mono"),
    ("tokyo", ATP, r"tokyo|japan open", "Ariake Coliseum", "boxed"),
    ("shanghai", ATP, r"shanghai", "Qizhong Arena", "mono"),
    ("almaty", ATP, r"almaty", "Almaty Arena", "mono"),
    ("lyon", ATP, r"\blyon\b|auvergne", "", "mono"),
    ("brussels", ATP, r"brussels|european open", "", "mono"),
    ("vienna", ATP, r"vienna|erste bank", "Wiener Stadthalle", "mono"),
    ("basel", ATP, r"basel|swiss indoors", "St. Jakobshalle", "mono"),
    ("paris", ATP, r"paris masters|rolex paris", "", "mono"),
    ("stockholm", ATP, r"stockholm", "Kungliga tennishallen", "mono"),
    # WTA events with a brand of their own: the venue only, the WTA's tier tag stays.
    ("wta_hobart", WTA, r"hobart", "Domain Tennis Centre", None),
    ("wta_abu_dhabi", WTA, r"abu dhabi", "Zayed Sports City", None),
    ("wta_doha", WTA, r"doha|qatar", "Khalifa Tennis Complex", None),
    ("wta_charleston", WTA, r"charleston", "Credit One Stadium", None),
    ("wta_linz", WTA, r"\blinz\b", "Design Center Linz", None),
    ("wta_stuttgart", WTA, r"stuttgart|porsche", "Porsche Arena", None),
    ("wta_rouen", WTA, r"rouen", "Kindarena", None),
    ("wta_strasbourg", WTA, r"strasbourg", "Tennis Club de Strasbourg", None),
    ("wta_nottingham", WTA, r"nottingham", "Nottingham Tennis Centre", None),
    ("wta_berlin", WTA, r"berlin", "LTTC Rot-Weiß Berlin", None),
    ("wta_hamburg", WTA, r"hamburg", "Am Rothenbaum", None),
    ("wta_toronto", WTA, r"toronto|national bank", "Sobeys Stadium", None),
    ("wta_monterrey", WTA, r"monterrey|abierto gnp", "Club Sonoma", None),
    ("wta_guadalajara", WTA, r"guadalajara", "Panamerican Tennis Center", None),
    ("wta_seoul", WTA, r"seoul|korea open", "Seoul Olympic Park", None),
    ("wta_wuhan", WTA, r"wuhan", "Optics Valley Tennis Center", None),
    ("wta_osaka", WTA, r"osaka|japan open", "Utsubo Tennis Center", None),
    ("wta_tokyo", WTA, r"tokyo|pan pacific", "Ariake Coliseum", None),
    ("wta_hong_kong", WTA, r"hong kong", "Victoria Park", None),
    ("wta_chennai", WTA, r"chennai", "SDAT Tennis Stadium", None),
)
_COMPILED = tuple((key, tours, re.compile(pattern), venue, mark) for key, tours, pattern, venue, mark in EVENTS)
# The venue by its own official logo where one is bundled (assets/logos, recorded in logos.source.json):
# the Royal Tennis Hall's crest from its own site, the others Wikimedia Commons' public-domain logos.
# Without one the card names the venue in words.
VENUE_MARKS = {
    "stockholm": "tennis_place_kungliga_tennishallen",
    "rotterdam": "tennis_place_rotterdam_ahoy",
    "vienna": "tennis_place_wiener_stadthalle",
    "miami": "tennis_place_hard_rock_stadium",
}
# Venue logos printed in their own colours on any ground (a night inking would wash out their details).
VENUE_MARKS_IN_COLOUR = {"tennis_place_kungliga_tennishallen", "tennis_place_hard_rock_stadium"}


def event_for(tournament):
    """{"key", "mark" (the bundled logo's name or None), "ink", "venue", "venue_mark" (the venue's bundled
    logo or None)} for the tournament, or None."""
    tournament = tournament or {}
    tour = str(tournament.get("tour") or "").upper()
    text = " ".join(str(tournament.get(field) or "") for field in ("name", "place", "city")).lower()
    if not text.strip():
        return None
    for key, tours, pattern, venue, mark in _COMPILED:
        if tour in tours and pattern.search(text):
            return {"key": key, "mark": f"tennis_event_{key}" if mark else None, "ink": mark, "venue": venue,
                    "venue_mark": VENUE_MARKS.get(key)}
    return None


def mark_keys():
    """Every bundled event mark's name (tennis_event_<key>)."""
    return [f"tennis_event_{key}" for key, _tours, _pattern, _venue, mark in EVENTS if mark]


def venue_mark_keys():
    """Every bundled venue logo's name (tennis_place_<venue>)."""
    return sorted(set(VENUE_MARKS.values()))
