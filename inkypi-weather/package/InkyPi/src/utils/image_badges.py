"""Compact, glyph-aligned labels that leave the underlying artwork visible."""

from PIL import Image, ImageDraw


def draw_image_badge(image, draw, xy, text, *, font, padding=(3, 2), radius=2):
    """Draw a translucent charcoal label; return inclusive bounds, or None for an empty label."""
    if text is None or text == "":
        return None
    text = str(text)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    pad_x, pad_y = padding
    width, height = right - left + pad_x * 2, bottom - top + pad_y * 2
    x, y = (round(value) for value in xy)
    plate = Image.new("RGBA", (width, height))
    ImageDraw.Draw(plate).rounded_rectangle(
        (0, 0, width - 1, height - 1), radius=radius, fill=(18, 26, 32, 204),
    )
    if image.mode == "RGBA":
        image.alpha_composite(plate, dest=(x, y))
    else:
        image.paste(plate, (x, y), plate)
    draw.text((x + pad_x - left, y + pad_y - top), text, fill=(255, 255, 255), font=font)
    return x, y, x + width - 1, y + height - 1
