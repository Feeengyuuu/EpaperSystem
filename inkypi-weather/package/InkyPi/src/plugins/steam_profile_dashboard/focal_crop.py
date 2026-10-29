"""Keep useful cover detail inside very shallow Steam recent-game strips.

Reviewed focal points are bound to both AppID and the exact original pixels.
New or changed covers use a bounded local detail-band estimate, without model
downloads, OCR, network requests or cache writes. This estimate is not a face
detector; reviewed eye/title positions provide precise control where available.
"""

import hashlib

from PIL import Image, ImageFilter, ImageOps


# Original official 460x215 covers, visually reviewed at native resolution.
# Values are pixel hash, focal y, and the feature retained by a shallow strip.
_REVIEWED_FOCUS = {
    "108600": ("6149dc8918796005ba517a4b7e68a8c720a58117840089db29f163d8519ff06a", 40 / 215, "face"),
    "1454400": ("266d5a3540ebe862825e04b87aec4e4ac24a01256c53cbdcf46f9037b4e2258c", 81 / 215, "title"),
    "1867240": ("3bf1b2fb8b7f9477b8ca241437945eb5c95d9bf0a5e664bda7e5458e9e41a1f3", 103 / 215, "title"),
    "292030": ("38f9b49f3d86d9331c8d543ee01c0e6ae7dfad4ec485abca441faa48125c724b", 53 / 215, "eyes"),
    "3751260": ("6834ff91534a18fc620a89a66d0d413e9960b83ba8fa47bdd697ba6942933966", 82 / 215, "eyes"),
    "431960": ("1ca15c6211fd0bd5323d86177fc16a27a7e1a1879026f1b95c5371346afcd920", 97 / 215, "title"),
    "730": ("0a831b89e52aa1c50b1e8ef5976b01f353c630b71e342aa8ba91da5692ff4f47", 44 / 215, "eyes"),
    # Reviewed 2026-10-28 for the in-game friend strips: Dota 2's title at the foot, EA FC 26's two
    # players' eyes (its title sits at the far left, under the friend's portrait), Phantom Blade
    # Zero's spaced title across the middle.
    "570": ("6188a85ed7e3e906ed6fe96dea800db8ed80dd43260bf0bddaebeb431527d736", 190 / 215, "title"),
    "3405690": ("7ec6d0cbe6c5aded6928c2bec12c101dbe8604522e197d7ca315b4d2f829a4f6", 40 / 215, "eyes"),
    "4115450": ("98f70df50f31bd1abac6c976ea88a32125125d660031d84d2d88c718fb49fa28", 110 / 215, "title"),
}
# A new cover's band is chosen by its edge detail plus how much it reads as lettering (sharp
# light/dark alternations along a row, as a game's title makes), with a light pull to the middle
# so a title near the top or foot can still win.
LETTERING_WEIGHT, LETTERING_STEP, CENTRE_PULL = 1.0, 38, 0.04


def _lettering(row):
    """How much a row of grey values reads as lettering: its sharp light/dark alternations."""
    sign, count = 0, 0
    for x in range(3, len(row) - 2):
        step = row[x] - row[x - 2]
        if abs(step) >= LETTERING_STEP:
            direction = 1 if step > 0 else -1
            if direction != sign:
                count += 1
                sign = direction
    return count


def _detail_focus(source, crop_fraction):
    """Find the horizontal band with the most detail and lettering (a title, a face's features),
    favouring the centre lightly and on ties."""
    thumb = source.convert("L")
    thumb.thumbnail((192, 192), Image.Resampling.LANCZOS)
    width, height = thumb.size
    if min(width, height) < 5:
        return 0.5
    # Suppress fine texture, then favour strong local edges (often lettering or
    # facial detail). Exclude the filter's artificial outer image boundary.
    soft = thumb.filter(ImageFilter.GaussianBlur(0.6))
    values, pixels = soft.filter(ImageFilter.FIND_EDGES).tobytes(), soft.tobytes()
    scores, letters = [0.0] * height, [0.0] * height
    for y in range(2, height - 2):
        scores[y] = sum((value / 255) ** 2 for value in values[y * width + 2:(y + 1) * width - 2])
        letters[y] = _lettering(pixels[y * width:(y + 1) * width])
    band = max(3, min(height - 4, round(height * crop_fraction)))

    def windows(series):
        integral = [0.0]
        for value in series:
            integral.append(integral[-1] + value)
        return [integral[start + band] - integral[start] for start in range(2, height - band - 1)]

    detail, lettering = windows(scores), windows(letters)
    maximum = max(detail, default=0)
    if maximum <= 1e-6:
        return 0.5
    most_letters = max(lettering) or 1
    candidates = [(detail[index] / maximum + LETTERING_WEIGHT * lettering[index] / most_letters,
                   (index + 2 + band / 2) / height) for index in range(len(detail))]
    return max(candidates, key=lambda item: (item[0] - CENTRE_PULL * abs(item[1] - 0.5),
                                            -abs(item[1] - 0.5)))[1]


def focused_cover_fit(source, size, appid):
    """Return an independent fitted image and truthful crop diagnostics.

    Only very wide rows change. Hero and TOP 3 crops retain their established
    framing. Focus denotes the crop's actual centre, not Pillow's offset ratio.
    """
    width, height = source.size
    crop_height = width * size[1] / size[0]
    if size[0] / size[1] < 5 or crop_height >= height:
        return ImageOps.fit(source, size, method=Image.Resampling.LANCZOS), {"mode": "center"}
    reviewed = _REVIEWED_FOCUS.get(str(appid))
    if (reviewed and source.size == (460, 215)
            and hashlib.sha256(source.convert("RGB").tobytes()).hexdigest() == reviewed[0]):
        focus, mode, feature = reviewed[1], "reviewed-focus", reviewed[2]
    else:
        focus, mode, feature = _detail_focus(source, crop_height / height), "detail-focus", "contrast"
    top = max(0.0, min(height - crop_height, focus * height - crop_height / 2))
    box = (0.0, top, float(width), top + crop_height)
    fitted = source.resize(size, Image.Resampling.LANCZOS, box=box)
    return fitted, {"mode": mode, "feature": feature, "focus_y": focus, "source_box": list(box)}
