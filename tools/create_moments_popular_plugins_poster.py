from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
INKYPI = ROOT / "inkypi-weather" / "package" / "InkyPi"
OUT = INKYPI / "docs" / "images" / "social"
RAW_IMG2 = OUT / "bases" / "img2-moments-popular-plugins-raw.png"
OUTPUT = OUT / "epapersystem-moments-popular-plugins.png"
IMG2_OUTPUT = OUT / "epapersystem-moments-popular-plugins-img2.png"
CANVAS = (1080, 1920)


def font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = [
        "msyhbd.ttc" if bold else "msyh.ttc",
        "simhei.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for name in candidates:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = image.size
    tw, th = size
    scale = max(tw / w, th / h)
    resized = image.resize((math.ceil(w * scale), math.ceil(h * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th)).convert("RGBA")


def text_width(draw: ImageDraw.ImageDraw, body: str, fnt: ImageFont.ImageFont) -> int:
    box = draw.textbbox((0, 0), body, font=fnt)
    return box[2] - box[0]


def chip(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, fill, fg, fnt) -> int:
    width = text_width(draw, label, fnt) + 36
    draw.rounded_rectangle((x, y, x + width, y + 46), radius=23, fill=fill)
    draw.text((x + 18, y + 8), label, font=fnt, fill=fg)
    return x + width + 14


def wrap_chars(draw: ImageDraw.ImageDraw, body: str, fnt: ImageFont.ImageFont, max_width: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for ch in body:
        candidate = current + ch
        if current and text_width(draw, candidate, fnt) > max_width:
            lines.append(current)
            current = ch
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def overlay_panel(base: Image.Image, box: tuple[int, int, int, int], radius: int, fill, blur: int = 0) -> None:
    layer = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    d.rounded_rectangle(box, radius=radius, fill=fill)
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(layer)


def main() -> None:
    if not RAW_IMG2.exists():
        raise FileNotFoundError(f"Missing img-2 raw poster source: {RAW_IMG2}")

    OUT.mkdir(parents=True, exist_ok=True)
    with Image.open(RAW_IMG2) as source:
        canvas = fit_cover(source, CANVAS)

    draw = ImageDraw.Draw(canvas)
    f92 = font(92, True)
    f62 = font(62, True)
    f32 = font(32, True)
    f28 = font(28)
    f24 = font(24, True)
    f21 = font(21)
    f18 = font(18)

    overlay_panel(canvas, (54, 62, 820, 520), 40, (255, 255, 255, 226))
    x = 86
    x = chip(draw, x, 94, "朋友圈首发", (18, 18, 18, 248), (255, 255, 255), f21)
    chip(draw, x, 94, "img-2 生成主视觉", (232, 237, 228, 255), (20, 20, 20), f21)

    draw.text((86, 170), "把墨水屏", font=f92, fill=(16, 16, 16))
    draw.text((86, 276), "变成私人信息台", font=f62, fill=(16, 16, 16))
    draw.text((90, 374), "流行插件页上线", font=f32, fill=(38, 38, 38))
    tagline = "热门插件、隐私插件、API Key 自管，一页发现，一键加入你的 EpaperSystem。"
    for index, line in enumerate(wrap_chars(draw, tagline, f28, 650)[:2]):
        draw.text((90, 426 + index * 38), line, font=f28, fill=(80, 80, 80))

    bx = 86
    for label, fill, fg in [
        ("AI日报", (239, 92, 78, 255), (20, 20, 20)),
        ("漫画封面", (22, 22, 22, 255), (255, 255, 255)),
        ("飞行雷达", (44, 130, 155, 255), (255, 255, 255)),
        ("隐私专区", (238, 220, 108, 255), (20, 20, 20)),
    ]:
        bx = chip(draw, bx, 546, label, fill, fg, f21)

    overlay_panel(canvas, (58, 1642, 1022, 1858), 34, (255, 255, 255, 232))
    draw.text((94, 1684), "隐私插件，也可以漂亮地上屏", font=f32, fill=(16, 16, 16))
    draw.text((96, 1734), "账号、日程、相册、资产概览用锁定态展示。", font=f24, fill=(72, 72, 72))
    draw.text((96, 1770), "公开宣传不暴露真实私人数据。", font=f24, fill=(72, 72, 72))
    draw.text((96, 1814), "基于开源 InkyPi 构建", font=f18, fill=(104, 104, 104))

    draw.rounded_rectangle((74, 1864, 1006, 1910), radius=23, fill=(18, 18, 18, 248))
    draw.text((104, 1874), "GitHub: Feeengyuuu/EpaperSystem", font=f21, fill=(255, 255, 255))

    canvas.convert("RGB").save(IMG2_OUTPUT, quality=95)
    canvas.convert("RGB").save(OUTPUT, quality=95)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "raw_img2_source": str(RAW_IMG2.relative_to(INKYPI)),
        "output": str(OUTPUT.relative_to(INKYPI)),
        "img2_output": str(IMG2_OUTPUT.relative_to(INKYPI)),
        "format": "WeChat Moments vertical poster, 1080x1920",
        "method": "img-2 generated the complete main visual; Python only overlays controlled Chinese typography and project link.",
        "privacy_rule": "Privacy plugins are represented as anonymized locked cards, not raw private data.",
        "upstream_credit": "Built on InkyPi: https://github.com/fatihak/InkyPi",
    }
    (OUT / "epapersystem-moments-popular-plugins.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(OUTPUT)


if __name__ == "__main__":
    main()
