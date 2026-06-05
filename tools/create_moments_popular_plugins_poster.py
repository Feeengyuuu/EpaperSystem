from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

try:
    import cv2
except Exception:  # pragma: no cover - poster can still render without OpenCV.
    cv2 = None


ROOT = Path(__file__).resolve().parents[1]
INKYPI = ROOT / "inkypi-weather" / "package" / "InkyPi"
OUT = INKYPI / "docs" / "images" / "social"
BASE = OUT / "bases" / "img2-moments-popular-plugins-bg.png"
README_SCREENS = INKYPI / "docs" / "images" / "readme" / "screens"

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


F96 = font(96, True)
F74 = font(74, True)
F52 = font(52, True)
F42 = font(42, True)
F34 = font(34, True)
F30 = font(30)
F27 = font(27, True)
F24 = font(24)
F22 = font(22, True)
F19 = font(19)
F16 = font(16)


def fit_cover(image: Image.Image, size: tuple[int, int]) -> Image.Image:
    w, h = image.size
    tw, th = size
    scale = max(tw / w, th / h)
    resized = image.resize((math.ceil(w * scale), math.ceil(h * scale)), Image.Resampling.LANCZOS)
    left = (resized.width - tw) // 2
    top = (resized.height - th) // 2
    return resized.crop((left, top, left + tw, top + th)).convert("RGBA")


def round_rect(draw: ImageDraw.ImageDraw, box, radius, fill, outline=None, width=1):
    draw.rounded_rectangle(box, radius=radius, fill=fill, outline=outline, width=width)


def text(draw: ImageDraw.ImageDraw, xy, body: str, fnt, fill=(20, 20, 20), **kwargs):
    draw.text(xy, body, font=fnt, fill=fill, **kwargs)


def text_width(draw: ImageDraw.ImageDraw, body: str, fnt) -> int:
    box = draw.textbbox((0, 0), body, font=fnt)
    return box[2] - box[0]


def wrap_text(draw: ImageDraw.ImageDraw, body: str, fnt, max_width: int) -> list[str]:
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


def paste_round(base: Image.Image, image: Image.Image, box: tuple[int, int, int, int], radius: int = 28) -> None:
    x1, y1, x2, y2 = box
    resized = image.resize((x2 - x1, y2 - y1), Image.Resampling.LANCZOS).convert("RGBA")
    mask = Image.new("L", resized.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, resized.width, resized.height), radius=radius, fill=255)
    resized.putalpha(mask)
    base.alpha_composite(resized, (x1, y1))


def paste_shadow(base: Image.Image, box, radius=30, opacity=80, blur=24, offset=(0, 12)):
    shadow = Image.new("RGBA", base.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    x1, y1, x2, y2 = box
    d.rounded_rectangle((x1 + offset[0], y1 + offset[1], x2 + offset[0], y2 + offset[1]), radius=radius, fill=(0, 0, 0, opacity))
    shadow = shadow.filter(ImageFilter.GaussianBlur(blur))
    base.alpha_composite(shadow)


def order_quad(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    return np.array([pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]], dtype=np.float32)


def paste_perspective(base: Image.Image, image: Image.Image, quad: list[tuple[int, int]]) -> None:
    if cv2 is None:
        x1 = min(p[0] for p in quad)
        x2 = max(p[0] for p in quad)
        y1 = min(p[1] for p in quad)
        y2 = max(p[1] for p in quad)
        paste_round(base, image, (x1, y1, x2, y2), radius=8)
        return

    src_img = image.convert("RGB").resize((800, 480), Image.Resampling.LANCZOS)
    src = np.array([[0, 0], [799, 0], [799, 479], [0, 479]], dtype=np.float32)
    dst = order_quad(np.array(quad, dtype=np.float32))
    matrix = cv2.getPerspectiveTransform(src, dst)
    warped = cv2.warpPerspective(np.array(src_img), matrix, base.size)
    mask = cv2.warpPerspective(np.full((480, 800), 255, dtype=np.uint8), matrix, base.size)
    out = np.array(base.convert("RGB"))
    out[mask > 2] = warped[mask > 2]
    base.paste(Image.fromarray(out).convert("RGBA"))


def draw_chip(draw: ImageDraw.ImageDraw, x: int, y: int, label: str, fill, fg=(20, 20, 20), fnt=F22) -> int:
    width = text_width(draw, label, fnt) + 34
    round_rect(draw, (x, y, x + width, y + 44), 22, fill)
    text(draw, (x + 17, y + 8), label, fnt, fg)
    return x + width + 12


def draw_phone_ui(canvas: Image.Image, box: tuple[int, int, int, int]) -> None:
    x1, y1, x2, y2 = box
    screen = Image.new("RGBA", (x2 - x1, y2 - y1), (248, 249, 245, 255))
    d = ImageDraw.Draw(screen)
    round_rect(d, (0, 0, screen.width - 1, screen.height - 1), 34, (248, 249, 245, 255), (35, 35, 35, 90), 2)
    text(d, (24, 30), "流行插件页", F30, (18, 18, 18))
    text(d, (24, 72), "Popular Plugins", F16, (108, 108, 108))
    tabs = [("热门", True), ("隐私", False), ("AI", False), ("生活", False)]
    tx = 24
    for label, selected in tabs:
        tw = text_width(d, label, F19) + 26
        fill = (21, 21, 21, 255) if selected else (229, 231, 226, 255)
        fg = (255, 255, 255) if selected else (60, 60, 60)
        round_rect(d, (tx, 114, tx + tw, 151), 18, fill)
        text(d, (tx + 13, 121), label, F19, fg)
        tx += tw + 9

    cards = [
        ("AI日报", "新闻 / 市场 / 总结", (221, 57, 57)),
        ("漫画封面", "日更三联封面", (34, 34, 34)),
        ("飞行雷达", "直播间状态墙", (35, 123, 156)),
        ("每日艺术", "美术馆轮播", (180, 136, 75)),
        ("Steam 私人档案", "游戏库与好友", (67, 97, 168)),
        ("私人日历", "本地显示日程", (56, 132, 88)),
    ]
    cy = 180
    for idx, (name, desc, color) in enumerate(cards):
        round_rect(d, (22, cy, screen.width - 22, cy + 72), 18, (255, 255, 255, 246), (218, 220, 216), 1)
        round_rect(d, (38, cy + 16, 78, cy + 56), 12, color + (255,))
        if idx >= 4:
            d.arc((49, cy + 25, 67, cy + 45), 180, 360, fill=(255, 255, 255), width=3)
            d.rectangle((47, cy + 36, 69, cy + 52), fill=(255, 255, 255))
        else:
            text(d, (48, cy + 20), "★", F19, (255, 255, 255))
        text(d, (92, cy + 13), name, F22, (22, 22, 22))
        text(d, (92, cy + 42), desc, F16, (112, 112, 112))
        text(d, (screen.width - 86, cy + 25), "安装", F16, (255, 255, 255))
        round_rect(d, (screen.width - 98, cy + 19, screen.width - 34, cy + 52), 16, (20, 20, 20, 255))
        cy += 86

    round_rect(d, (22, screen.height - 86, screen.width - 22, screen.height - 28), 18, (20, 20, 20, 255))
    text(d, (42, screen.height - 73), "一键安装  ·  API Key 自管", F19, (255, 255, 255))
    canvas.alpha_composite(screen, (x1, y1))


def draw_plugin_thumbnail_card(canvas: Image.Image, box, title: str, subtitle: str, image_path: Path, accent) -> None:
    paste_shadow(canvas, box, radius=26, opacity=42, blur=22, offset=(0, 10))
    d = ImageDraw.Draw(canvas)
    round_rect(d, box, 26, (255, 255, 255, 238), (222, 222, 218), 1)
    x1, y1, x2, y2 = box
    with Image.open(image_path) as img:
        paste_round(canvas, fit_cover(img.convert("RGB"), (x2 - x1 - 36, 116)), (x1 + 18, y1 + 18, x2 - 18, y1 + 134), 18)
    round_rect(d, (x1 + 18, y2 - 64, x1 + 26, y2 - 28), 4, accent)
    text(d, (x1 + 38, y2 - 70), title, F24, (22, 22, 22))
    text(d, (x1 + 38, y2 - 38), subtitle, F16, (96, 96, 96))


def draw_privacy_card(draw: ImageDraw.ImageDraw, box, title: str, desc: str, accent) -> None:
    x1, y1, x2, y2 = box
    round_rect(draw, box, 22, (250, 250, 247, 238), (220, 220, 216), 1)
    round_rect(draw, (x1 + 20, y1 + 22, x1 + 72, y1 + 74), 16, accent)
    draw.arc((x1 + 36, y1 + 36, x1 + 56, y1 + 56), 180, 360, fill=(255, 255, 255), width=4)
    draw.rectangle((x1 + 33, y1 + 49, x1 + 59, y1 + 67), fill=(255, 255, 255))
    text(draw, (x1 + 90, y1 + 18), title, F24, (20, 20, 20))
    for idx, line in enumerate(wrap_text(draw, desc, F16, x2 - x1 - 115)[:2]):
        text(draw, (x1 + 90, y1 + 50 + idx * 22), line, F16, (88, 88, 88))
    round_rect(draw, (x2 - 92, y1 + 20, x2 - 20, y1 + 50), 15, (22, 22, 22, 235))
    text(draw, (x2 - 78, y1 + 25), "本地", F16, (255, 255, 255))


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    with Image.open(BASE) as base:
        canvas = fit_cover(base, CANVAS)
    draw = ImageDraw.Draw(canvas)

    # Soft readability panels.
    top_panel = Image.new("RGBA", CANVAS, (0, 0, 0, 0))
    td = ImageDraw.Draw(top_panel)
    td.rounded_rectangle((54, 62, 760, 560), radius=42, fill=(255, 255, 255, 226))
    top_panel = top_panel.filter(ImageFilter.GaussianBlur(0.2))
    canvas.alpha_composite(top_panel)

    x = 86
    y = 92
    draw_chip(draw, x, y, "朋友圈首发", (20, 20, 20, 245), (255, 255, 255), F19)
    draw_chip(draw, x + 142, y, "流行插件页上线", (231, 236, 225, 255), (20, 20, 20), F19)

    text(draw, (86, 162), "把墨水屏", F74, (18, 18, 18))
    text(draw, (86, 246), "变成私人信息台", F74, (18, 18, 18))
    text(draw, (90, 354), "开源 · 插件化 · 中文安装", F30, (76, 76, 76))
    for idx, line in enumerate(wrap_text(draw, "热门插件、隐私插件、API Key 自管，一页发现，一键加入你的 EpaperSystem。", F24, 600)[:3]):
        text(draw, (90, 410 + idx * 34), line, F24, (82, 82, 82))

    cx = 86
    for label, fill in [
        ("AI日报", (239, 98, 83, 255)),
        ("漫画封面", (24, 24, 24, 255)),
        ("飞行雷达", (49, 128, 151, 255)),
        ("隐私专区", (238, 218, 121, 255)),
    ]:
        cx = draw_chip(draw, cx, 506, label, fill, (255, 255, 255) if fill[0] < 80 else (20, 20, 20), F19)

    # Phone mock: popular plugins page.
    draw_phone_ui(canvas, (94, 712, 407, 1337))

    # Device screen: real plugin output from the actual frame.
    with Image.open(README_SCREENS / "actual-daily-ai-news-800x480.png") as device_screen:
        paste_perspective(canvas, device_screen, [(418, 883), (1002, 909), (975, 1280), (411, 1221)])

    # Floating popular plugin cards.
    draw_plugin_thumbnail_card(
        canvas,
        (610, 560, 1000, 758),
        "真实插件输出",
        "来自 ColoredEpaperFrame",
        README_SCREENS / "actual-lol-info-800x480.png",
        (40, 152, 205, 255),
    )
    draw_plugin_thumbnail_card(
        canvas,
        (644, 774, 1000, 970),
        "漫画封面",
        "GCD / Comic Covers",
        README_SCREENS / "actual-comic-covers-800x480.png",
        (36, 36, 36, 255),
    )

    # Bottom information band.
    paste_shadow(canvas, (58, 1382, 1022, 1812), radius=34, opacity=45, blur=22, offset=(0, 10))
    round_rect(draw, (58, 1382, 1022, 1812), 34, (255, 255, 255, 232), (218, 218, 214), 1)
    text(draw, (92, 1424), "隐私插件，也可以漂亮地上屏", F42, (18, 18, 18))
    text(draw, (94, 1482), "账号、日程、相册、资产概览都由你自己掌控。", F24, (78, 78, 78))
    text(draw, (94, 1516), "公开宣传只展示打码预览，真实数据留在本地。", F24, (78, 78, 78))

    privacy_cards = [
        ((92, 1574, 530, 1662), "Steam 私人档案", "游戏库、好友、在线状态，按你的方式展示。", (55, 92, 166, 255)),
        ((552, 1574, 986, 1662), "私人日历", "Google / iCal 日程可只在家中屏幕显示。", (58, 132, 92, 255)),
        ((92, 1684, 530, 1772), "本地相册", "家人照片、旅行相册，自动排版轮播。", (189, 117, 68, 255)),
        ((552, 1684, 986, 1772), "资产概览", "自选股和组合数据，密钥自己保管。", (33, 33, 33, 255)),
    ]
    for item in privacy_cards:
        draw_privacy_card(draw, *item)

    # CTA footer.
    round_rect(draw, (70, 1838, 1010, 1894), 28, (20, 20, 20, 250))
    text(draw, (100, 1851), "GitHub 现在开放：Feeengyuuu/EpaperSystem", F24, (255, 255, 255))
    text(draw, (100, 1898), "基于开源 InkyPi 构建  ·  Raspberry Pi / Waveshare / Plugins", F16, (82, 82, 82))

    out = OUT / "epapersystem-moments-popular-plugins.png"
    canvas.convert("RGB").save(out, quality=95)

    manifest = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "output": str(out.relative_to(INKYPI)),
        "base": str(BASE.relative_to(INKYPI)),
        "format": "WeChat Moments vertical poster, 1080x1920",
        "privacy_rule": "Private plugin content is shown as anonymized/locked UI, not raw private data.",
        "live_sources": [
            "docs/images/readme/screens/actual-daily-ai-news-800x480.png",
            "docs/images/readme/screens/actual-lol-info-800x480.png",
            "docs/images/readme/screens/actual-comic-covers-800x480.png",
        ],
        "upstream_credit": "Built on InkyPi: https://github.com/fatihak/InkyPi",
    }
    (OUT / "epapersystem-moments-popular-plugins.manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(out)


if __name__ == "__main__":
    main()
