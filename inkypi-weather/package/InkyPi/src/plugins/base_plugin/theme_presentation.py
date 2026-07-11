"""Pixel-safe presentation helpers for plugin theme chrome."""

from __future__ import annotations

from PIL import Image, ImageDraw


MIN_MEDIA_DIMENSION = 18
MEDIA_INSET = 8


def apply_media_theme_chrome(image, plugin_id, theme, dimensions):
    """Wrap full-display media in theme chrome without resampling its center."""
    width, height = _validate_dimensions(plugin_id, dimensions)
    if image.size != (width, height):
        source_width, source_height = image.size
        raise ValueError(
            f"{plugin_id} media theme chrome requires source size "
            f"{width}x{height}; got {source_width}x{source_height}"
        )

    source_info = dict(image.info)
    source_rgb = image.convert("RGB")
    palette = theme["palette"]
    canvas = Image.new(
        "RGB",
        (width, height),
        tuple(palette["background"]),
    )
    inner_box = (
        MEDIA_INSET,
        MEDIA_INSET,
        width - MEDIA_INSET,
        height - MEDIA_INSET,
    )
    canvas.paste(source_rgb.crop(inner_box), (MEDIA_INSET, MEDIA_INSET))
    ImageDraw.Draw(canvas).rectangle(
        (6, 6, width - 7, height - 7),
        outline=tuple(palette["accent"]),
        width=2,
    )
    canvas.info.update(source_info)
    return canvas


def _validate_dimensions(plugin_id, dimensions):
    try:
        width, height = dimensions
    except (TypeError, ValueError) as error:
        raise ValueError(
            f"{plugin_id} media theme chrome dimensions must be two integers "
            f"at least {MIN_MEDIA_DIMENSION}x{MIN_MEDIA_DIMENSION}"
        ) from error

    if any(
        not isinstance(value, int) or isinstance(value, bool)
        for value in (width, height)
    ):
        raise ValueError(
            f"{plugin_id} media theme chrome dimensions must be two integers "
            f"at least {MIN_MEDIA_DIMENSION}x{MIN_MEDIA_DIMENSION}"
        )
    if width < MIN_MEDIA_DIMENSION or height < MIN_MEDIA_DIMENSION:
        raise ValueError(
            f"{plugin_id} media theme chrome dimensions must be at least "
            f"{MIN_MEDIA_DIMENSION}x{MIN_MEDIA_DIMENSION}; got {width}x{height}"
        )
    return width, height
