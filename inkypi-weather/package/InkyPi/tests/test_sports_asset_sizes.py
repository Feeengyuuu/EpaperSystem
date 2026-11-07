"""The Sports Dashboard's league logos stay small enough to decode cheaply in its memory-guarded
worker: they are drawn at most about 100 px, and a 960 x 2177 NBA logo decoded at 8 MB each time
it was drawn at another size, enough to push the lower region past the guard."""

from PIL import Image

from plugins.sports_dashboard.sports_dashboard import SportsDashboard

LONG_SIDE_LIMIT = 600


def test_league_logos_are_stored_at_most_600_px_on_their_long_side():
    oversized = {}
    for sport in ("NBA", "MLB", "WNBA", "NHL", "PGA", "NFL", "NCAA"):
        path = SportsDashboard._sport_logo_path(sport)
        with Image.open(path) as logo:
            if max(logo.size) > LONG_SIDE_LIMIT:
                oversized[sport] = logo.size
    assert oversized == {}
