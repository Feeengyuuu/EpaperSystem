"""Reading contrast and continuous pictures on the fixed-size news page."""

from PIL import Image, ImageChops
import pytest

from plugins.daily_ai_news import news_layout
from tests.test_daily_ai_news import _plugin


def _page(mode="day"):
    plugin = _plugin()
    palette = plugin._page_palette({"mode": mode})
    canvas = Image.new("RGB", (800, 480), palette["background"])
    return news_layout.NewsPage(plugin, canvas, palette, "Microsoft YaHei")


def _stories(count=4):
    return [
        {"title": f"全国新闻报道{index}", "why": "相关工作正在有序推进", "tag": "经济"}
        for index in range(count)
    ]


def _render(page, photo=None):
    lead = {"title": "今日新闻", "why": "持续关注重要消息", "section_label": "国内", "tag": "经济"}
    if photo is not None:
        lead["photo"] = photo
    page.render(
        title="整点新闻",
        lead=lead,
        mainland=_stories(),
        world=_stories(),
        quotes={},
        meta={"date_text": "11月15日 周日", "detail": "07:47 更新", "source_logos": ["bbc_news"]},
    )
    return page.img


def _luminance(rgb):
    channels = [value / 255 for value in rgb[:3]]
    linear = [value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4 for value in channels]
    return sum(value * weight for value, weight in zip(linear, (0.2126, 0.7152, 0.0722)))


def _contrast(first, second):
    lighter, darker = sorted((_luminance(first), _luminance(second)), reverse=True)
    return (lighter + 0.05) / (darker + 0.05)


@pytest.mark.parametrize("mode", ["day", "night"])
def test_lead_backdrop_reaches_news_columns_without_changing_header_or_markets(mode):
    plain = _render(_page(mode))
    pictured = _render(_page(mode), Image.new("RGB", (720, 240), (20, 130, 205)))

    header = (0, 0, 800, news_layout.RULE_Y + 1)
    markets = (0, news_layout.MARKET_TOP - 2, 800, 480)
    assert ImageChops.difference(plain.crop(header), pictured.crop(header)).getbbox() is None
    assert ImageChops.difference(plain.crop(markets), pictured.crop(markets)).getbbox() is None
    foot = (0, news_layout.COLUMN_BOTTOM, 800, news_layout.MARKET_TOP - 2)
    assert ImageChops.difference(plain.crop(foot), pictured.crop(foot)).getbbox() is None

    # The image survives the column's reading surface instead of stopping at the lead band.
    below_lead = (news_layout.MARGIN, news_layout.COLUMN_TOP + 35, 300, news_layout.COLUMN_TOP + 80)
    assert ImageChops.difference(plain.crop(below_lead), pictured.crop(below_lead)).getbbox() is not None


@pytest.mark.parametrize("mode", ["day", "night"])
def test_lead_backdrop_fades_continuously_to_the_page_at_the_column_foot(mode):
    page = _page(mode)
    paper = page.c["background"]
    picture = Image.new("RGB", (720, 240), page.c["ink"])
    page.lead_photo(picture, (13, 63, 780, news_layout.COLUMN_BOTTOM + 1), fade_below=news_layout.LEAD_BOX[1])
    samples = [page.img.getpixel((35, y)) for y in range(news_layout.LEAD_BOX[1], news_layout.COLUMN_BOTTOM + 1)]
    distances = [sum(abs(a - b) for a, b in zip(sample, paper)) for sample in samples]
    assert distances[0] > 0 and distances[-1] == 0
    assert all(first >= second for first, second in zip(distances, distances[1:]))
    assert min(_contrast(page.c["ink"], colour) for colour in samples) >= 4.5


@pytest.mark.parametrize("mode", ["day", "night"])
def test_photos_leave_body_text_readable_even_with_the_worst_picture_colour(monkeypatch, mode):
    page = _page(mode)
    # A picture the same colour as the letters is the worst case for reading them.
    picture = Image.new("RGB", (720, 240), page.c["ink"])
    page.lead_photo(
        picture,
        (news_layout.MARGIN - news_layout.LEAD_PHOTO_PAD, 63, 780, news_layout.COLUMN_BOTTOM + 1),
        fade_below=news_layout.COLUMN_TOP,
    )
    stories = [dict(item, photo=picture, photo_url="same-picture") for item in _stories()]
    # Inspect the ground on which letters would be drawn, including the ends of long lines.
    monkeypatch.setattr(page, "text", lambda *args, **kwargs: None)
    width = (800 - 2 * news_layout.MARGIN - news_layout.COLUMN_GAP) // 2
    page.column(news_layout.MARGIN, width, "国内", stories, sources=[])
    reading_area = page.img.crop((
        news_layout.MARGIN + 26,
        news_layout.COLUMN_TOP + 32,
        news_layout.MARGIN + width - news_layout.STORY_ICON_SLOT + 1,
        news_layout.COLUMN_BOTTOM,
    ))
    colours = {rgb for _count, rgb in reading_area.getcolors(reading_area.width * reading_area.height)}
    assert min(_contrast(page.c["ink"], colour) for colour in colours) >= 4.5


@pytest.mark.parametrize(
    ("urls", "expected_runs"),
    [
        (["shared", "shared", "different", "shared"], [2, 1, 1]),
        ([None, None, None, None], [1, 1, 1, 1]),
    ],
)
def test_only_adjacent_stories_with_the_same_known_photo_are_combined(monkeypatch, urls, expected_runs):
    page = _page()
    photos = [Image.new("RGB", (152, 120), colour) for colour in
              ((200, 30, 50), (200, 30, 50), (20, 90, 200), (200, 30, 50))]
    stories = []
    for item, url, photo in zip(_stories(), urls, photos):
        story = dict(item, photo=photo)
        if url is not None:
            story["photo_url"] = url
        stories.append(story)
    drawn = []
    original = page.story_photo

    def record_photo(photo, box):
        drawn.append((photo, box))
        original(photo, box)

    monkeypatch.setattr(page, "story_photo", record_photo)
    width = (800 - 2 * news_layout.MARGIN - news_layout.COLUMN_GAP) // 2
    page.column(news_layout.MARGIN, width, "国内", stories, sources=[])

    assert len(drawn) == len(expected_runs)
    offset = 0
    for (photo, box), run in zip(drawn, expected_runs):
        assert photo is photos[offset]
        assert box[3] - box[1] >= run * news_layout.STORY_PHOTO_MIN_HEIGHT
        # Different pictures remain visible in their own row, even when an earlier URL returns.
        pixel = page.img.getpixel((round(box[2]) - 5, round((box[1] + box[3]) / 2)))
        assert pixel == photos[offset].getpixel((0, 0))
        offset += run
    # Existing row bleeds overlap by two pixels to avoid a paper seam.
    assert all(first[1][3] <= second[1][1] + 2 for first, second in zip(drawn, drawn[1:]))
