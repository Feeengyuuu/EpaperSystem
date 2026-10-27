"""Polymarket-local brand treatment; never mutates the launch-page palette.

Official primary color and monochrome logo guidance: https://polymarket.com/brand
The quiet body tints below are UI adaptations, not additional official brand colors.
"""

POLY_BLUE = (46, 92, 255)  # Official #2E5CFF
POLY_WHITE = (255, 255, 255)
BRAND_FADE_HEIGHT = 144


def market_palette(page_palette):
    night = sum(page_palette["paper"]) < 384
    if night:
        colors = dict(paper=(16, 31, 69), panel=(23, 42, 84), ink=(242, 246, 255),
                      muted=(184, 202, 231), rule=(74, 101, 153), blue=(139, 172, 255),
                      green=(114, 224, 170), red=(255, 143, 150), orange=(255, 170, 96),
                      white=(16, 31, 69))
    else:
        colors = dict(paper=(236, 242, 255), panel=(248, 250, 255), ink=(14, 34, 71),
                      muted=(68, 86, 117), rule=(168, 185, 218), blue=POLY_BLUE,
                      green=(8, 120, 61), red=(190, 25, 45), orange=(190, 64, 10),
                      white=POLY_WHITE)
    return {**page_palette, **colors}


def market_background_color(body_palette, offset):
    """A quiet wash of official blue, easing downward into the panel's own paper."""
    paper = body_palette["paper"]
    strength = .28 if sum(paper) < 384 else .22
    progress = max(0., min(1., offset / BRAND_FADE_HEIGHT))
    amount = strength * (1 - progress) ** 2
    return tuple(round(base + (brand - base) * amount) for base, brand in zip(paper, POLY_BLUE))


def market_header_palette(body_palette):
    # Dark day ink / light night ink stays readable over every part of the wash.
    return {**body_palette, "paper": market_background_color(body_palette, 0)}
