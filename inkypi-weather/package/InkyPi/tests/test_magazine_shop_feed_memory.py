"""Magazine Covers reads a shop's product feed for a cover's facts without keeping the feed.

The plugin object lives as long as the service. On 2026-11-07 it was holding every feed page it
had read (250 full product JSONs a page, descriptions included), about 5 MB after one playlist
pass, and reusing them on later days. A page is now read once per refresh and kept only as
file name -> facts for a short while.
"""

import json

from plugins.magazine_covers import magazine_covers as module
from plugins.magazine_covers.magazine_covers import MagazineCovers

SOURCE = {"name": "Newsweek", "url": "https://magazineshop.us/collections/newsweek"}
COVER = "https://magazineshop.us/cdn/shop/files/newsweek-1107.jpg?v=3"


def product(number, image, title="Newsweek - The Election Issue"):
    return {"title": title, "vendor": "Newsweek", "product_type": "261107", "published_at": "2026-11-01T00:00:00Z",
            "body_html": "<p>" + "long description " * 200 + "</p>",
            "variants": [{"price": "8.99"}], "images": [{"src": f"https://cdn.shopify.com/s/files/{image}"}],
            "id": number}


def plugin_with_feed(pages, clock):
    plugin = MagazineCovers({"id": "magazine_covers"})
    fetched = []

    def fetch_text(url, source=None, deadline=None):
        fetched.append(url)
        number = int(url.rsplit("page=", 1)[1])
        return json.dumps({"products": pages.get(number, [])})

    plugin._fetch_text = fetch_text
    plugin._monotonic = lambda: clock[0]
    return plugin, fetched


def test_a_cover_on_the_feed_gets_its_facts_and_the_feed_is_not_kept_whole():
    clock = [1000.0]
    plugin, fetched = plugin_with_feed({1: [product(1, "other.jpg"), product(2, "newsweek-1107.jpg")]}, clock)

    facts = plugin._shop_product_facts(SOURCE, COVER)

    assert facts["publication"] == "Newsweek" and facts["issue"] == "The Election Issue"
    assert facts["issue_date"] == "2026-11-07" and facts["price"] == "$8.99"
    assert len(fetched) == 1
    assert "long description" not in repr(plugin.__dict__)


def test_the_feed_is_read_once_per_refresh_and_again_on_a_later_one():
    clock = [1000.0]
    plugin, fetched = plugin_with_feed({1: [product(2, "newsweek-1107.jpg")]}, clock)

    plugin._shop_product_facts(SOURCE, COVER)
    plugin._shop_product_facts(SOURCE, "https://magazineshop.us/cdn/shop/files/missing.jpg")
    assert len(fetched) == 1

    clock[0] += module.SHOP_FEED_MEMO_SECONDS + 1
    plugin._shop_product_facts(SOURCE, COVER)
    assert len(fetched) == 2


def test_a_full_page_leads_to_the_next_and_the_first_listed_product_wins():
    clock = [0.0]
    first = [product(index, f"filler-{index}.jpg") for index in range(250)]
    second = [product(900, "newsweek-1107.jpg", "Newsweek - First"), product(901, "newsweek-1107.jpg", "Newsweek - Second")]
    plugin, fetched = plugin_with_feed({1: first, 2: second}, clock)

    assert plugin._shop_product_facts(SOURCE, COVER)["issue"] == "First"
    assert [url.rsplit("page=", 1)[1] for url in fetched] == ["1", "2"]
