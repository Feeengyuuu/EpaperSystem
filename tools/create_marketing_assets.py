from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "marketing_assets"
SCREENS = OUT / "source_screens"
MOCKUPS = OUT / "mockups"


@dataclass(frozen=True)
class ScreenSpec:
    slug: str
    label: str
    source: Path


SCREEN_SPECS = [
    ScreenSpec("weather", "天气", ROOT / ".tmp" / "context_weather.png"),
    ScreenSpec("calendar", "日历", ROOT / ".tmp" / "simple_calendar_after.png"),
    ScreenSpec("pet", "AI 宠物", ROOT / ".tmp" / "epaper_pet_robot_hunting_final.png"),
    ScreenSpec("poem", "文学时钟", ROOT / ".tmp" / "chinese_literature_clock_current_font_fallback.png"),
    ScreenSpec("steam-profile-demo", "游戏档案", ROOT / ".tmp" / "steam_profile_dashboard_zh_default_id_smoke.png"),
    ScreenSpec("daily-news", "新闻简报", ROOT / ".tmp" / "context_daily_ai_news.png"),
]


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    names = [
        "msyhbd.ttc" if bold else "msyh.ttc",
        "simhei.ttf",
        "arialbd.ttf" if bold else "arial.ttf",
    ]
    for name in names:
        path = Path("C:/Windows/Fonts") / name
        if path.exists():
            return ImageFont.truetype(str(path), size)
    return ImageFont.load_default()


def text_size(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def make_dirs() -> None:
    SCREENS.mkdir(parents=True, exist_ok=True)
    MOCKUPS.mkdir(parents=True, exist_ok=True)


def copy_source_screens() -> dict[str, Path]:
    result: dict[str, Path] = {}
    for spec in SCREEN_SPECS:
        if not spec.source.exists():
            raise FileNotFoundError(f"Missing source screenshot: {spec.source}")
        with Image.open(spec.source) as im:
            if im.size != (800, 480):
                raise ValueError(f"{spec.source} is {im.size}, expected 800x480")
        dest = SCREENS / f"{spec.slug}-800x480.png"
        shutil.copy2(spec.source, dest)
        result[spec.slug] = dest
    return result


def rounded_shadow(size: tuple[int, int], radius: int, blur: int, alpha: int) -> Image.Image:
    w, h = size
    shadow = Image.new("RGBA", (w + blur * 4, h + blur * 4), (0, 0, 0, 0))
    d = ImageDraw.Draw(shadow)
    d.rounded_rectangle((blur * 2, blur * 2, blur * 2 + w, blur * 2 + h), radius=radius, fill=(0, 0, 0, alpha))
    return shadow.filter(ImageFilter.GaussianBlur(blur))


def device_image(screen_path: Path, screen_w: int) -> Image.Image:
    screen_h = round(screen_w * 480 / 800)
    bezel = max(18, round(screen_w * 0.035))
    foot_h = max(40, round(screen_w * 0.09))
    w = screen_w + bezel * 2
    h = screen_h + bezel * 2 + foot_h
    pad = max(36, round(screen_w * 0.06))

    group = Image.new("RGBA", (w + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow = rounded_shadow((w, screen_h + bezel * 2), radius=34, blur=28, alpha=92)
    group.alpha_composite(shadow, (pad - 56, pad - 38))

    d = ImageDraw.Draw(group)
    frame = (pad, pad, pad + w, pad + screen_h + bezel * 2)
    d.rounded_rectangle(frame, radius=34, fill=(18, 18, 18, 255))
    d.rounded_rectangle((frame[0] + 7, frame[1] + 7, frame[2] - 7, frame[3] - 7), radius=26, outline=(50, 50, 50, 255), width=3)

    with Image.open(screen_path) as source:
        screen = source.convert("RGB").resize((screen_w, screen_h), Image.Resampling.LANCZOS)
    group.alpha_composite(screen.convert("RGBA"), (pad + bezel, pad + bezel))

    # Simple desktop stand behind the screen. It is outside the actual 800x480 content.
    stand_y = pad + screen_h + bezel * 2 - 2
    d.rounded_rectangle(
        (pad + w * 0.43, stand_y - 5, pad + w * 0.57, stand_y + foot_h * 0.72),
        radius=16,
        fill=(28, 28, 28, 255),
    )
    d.rounded_rectangle(
        (pad + w * 0.32, stand_y + foot_h * 0.58, pad + w * 0.68, stand_y + foot_h * 0.9),
        radius=18,
        fill=(20, 20, 20, 255),
    )

    return group


def add_gradient_bg(img: Image.Image, top: tuple[int, int, int], bottom: tuple[int, int, int]) -> None:
    d = ImageDraw.Draw(img)
    w, h = img.size
    for y in range(h):
        t = y / max(1, h - 1)
        color = tuple(round(top[i] * (1 - t) + bottom[i] * t) for i in range(3))
        d.line((0, y, w, y), fill=color)


def make_hero(screens: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1920, 1080), (242, 239, 231))
    add_gradient_bg(canvas, (247, 246, 241), (225, 231, 232))
    d = ImageDraw.Draw(canvas)
    d.rectangle((0, 850, 1920, 1080), fill=(203, 188, 164))
    d.rounded_rectangle((118, 120, 810, 680), radius=30, fill=(255, 255, 255, 96), outline=(225, 222, 213), width=2)

    d.text((150, 166), "真实 800x480", fill=(18, 18, 18), font=font(78, True))
    d.text((150, 262), "横屏 e-paper 信息台", fill=(18, 18, 18), font=font(70, True))
    d.text((154, 374), "天气、日历、新闻、游戏状态和 AI 宠物\n自动轮换显示，屏幕内容来自项目真实截图。", fill=(68, 68, 68), font=font(35))

    chips = ["低功耗", "无前置按钮", "自动轮换", "桌面常亮"]
    x = 154
    for chip in chips:
        tw, th = text_size(d, chip, font(29, True))
        d.rounded_rectangle((x, 540, x + tw + 42, 594), radius=22, fill=(20, 20, 20))
        d.text((x + 21, 548), chip, fill=(255, 255, 255), font=font(29, True))
        x += tw + 62

    dev = device_image(screens["weather"], 980)
    canvas.paste(dev, (805, 174), dev)
    canvas.save(MOCKUPS / "hero-actual-800x480-screen.png", quality=95)


def make_feature_grid(screens: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1920, 1080), (245, 245, 243))
    d = ImageDraw.Draw(canvas)
    d.text((84, 62), "真实屏幕内容，不用生成式 UI 替代", fill=(18, 18, 18), font=font(54, True))
    d.text((88, 130), "每个设备屏幕均来自 800x480 横向截图，外层仅做宣传排版。", fill=(86, 86, 86), font=font(31))

    cards = [
        ("weather", "天气面板"),
        ("calendar", "日历面板"),
        ("pet", "AI 宠物"),
        ("steam-profile-demo", "游戏档案"),
    ]
    positions = [(72, 220), (984, 220), (72, 640), (984, 640)]
    for (slug, label), pos in zip(cards, positions):
        x, y = pos
        d.rounded_rectangle((x, y, x + 864, y + 340), radius=24, fill=(255, 255, 255), outline=(226, 226, 226), width=2)
        dev = device_image(screens[slug], 430)
        canvas.paste(dev, (x + 22, y + 16), dev)
        d.text((x + 540, y + 78), label, fill=(22, 22, 22), font=font(44, True))
        d.text((x + 540, y + 146), "800 x 480\n横向截图\n可替换真实场景", fill=(88, 88, 88), font=font(30))
    canvas.save(MOCKUPS / "feature-grid-actual-screens.png", quality=95)


def make_social_portrait(screens: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1080, 1350), (230, 235, 232))
    add_gradient_bg(canvas, (250, 249, 244), (215, 226, 224))
    d = ImageDraw.Draw(canvas)
    d.text((72, 86), "桌面上的\n常亮信息窗口", fill=(16, 16, 16), font=font(72, True))
    d.text((76, 278), "真实 800x480 横屏显示\n适合家庭、工作台和小型工作室", fill=(73, 73, 73), font=font(32))
    dev = device_image(screens["pet"], 860)
    canvas.paste(dev, (60, 436), dev)
    d.rounded_rectangle((82, 1212, 998, 1282), radius=34, fill=(18, 18, 18))
    d.text((126, 1228), "无前置按钮 · 自动轮换 · e-paper 视觉", fill=(255, 255, 255), font=font(32, True))
    canvas.save(MOCKUPS / "social-portrait-actual-screen.png", quality=95)


def make_marketplace_square(screens: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1600, 1600), (246, 246, 244))
    d = ImageDraw.Draw(canvas)
    d.text((92, 80), "E-paper Smart Dashboard Kit", fill=(18, 18, 18), font=font(58, True))
    d.text((96, 154), "屏幕内容为真实 800x480 项目截图", fill=(84, 84, 84), font=font(34))

    # Generic unbranded package.
    d.rounded_rectangle((1020, 348, 1434, 1030), radius=22, fill=(255, 255, 255), outline=(212, 212, 212), width=3)
    d.rectangle((1058, 388, 1396, 646), fill=(238, 238, 234))
    d.text((1086, 694), "Smart\nDashboard\nKit", fill=(38, 38, 38), font=font(48, True), spacing=8)
    d.text((1088, 906), "7.5 inch e-paper", fill=(98, 98, 98), font=font(27))

    dev = device_image(screens["calendar"], 900)
    canvas.paste(dev, (48, 470), dev)
    d.rounded_rectangle((350, 1238, 1120, 1284), radius=24, outline=(80, 80, 80), width=10)
    d.arc((290, 1115, 760, 1368), 198, 350, fill=(40, 40, 40), width=10)
    d.rounded_rectangle((180, 1320, 1420, 1430), radius=36, fill=(255, 255, 255), outline=(222, 222, 222), width=2)
    d.text((244, 1348), "设备框 / 支架 / USB-C 线 / 快速上手卡", fill=(46, 46, 46), font=font(35, True))
    canvas.save(MOCKUPS / "marketplace-kit-actual-screen.png", quality=95)


def make_screen_strip(screens: dict[str, Path]) -> None:
    canvas = Image.new("RGB", (1920, 1080), (20, 20, 20))
    d = ImageDraw.Draw(canvas)
    d.text((70, 60), "800x480 横向实际截图素材", fill=(255, 255, 255), font=font(52, True))
    d.text((74, 128), "这些源图可作为后续 img-2 场景图的屏幕贴图，不能让模型重绘。", fill=(198, 198, 198), font=font(30))
    entries = [
        ("weather", "天气"),
        ("calendar", "日历"),
        ("pet", "AI 宠物"),
        ("poem", "文学时钟"),
        ("steam-profile-demo", "游戏档案"),
        ("daily-news", "新闻简报"),
    ]
    for idx, (slug, label) in enumerate(entries):
        col = idx % 3
        row = idx // 3
        x = 72 + col * 610
        y = 220 + row * 390
        with Image.open(screens[slug]) as im:
            thumb = im.convert("RGB").resize((500, 300), Image.Resampling.LANCZOS)
        d.rounded_rectangle((x - 18, y - 18, x + 518, y + 352), radius=20, fill=(44, 44, 44))
        canvas.paste(thumb, (x, y))
        d.text((x, y + 316), label, fill=(255, 255, 255), font=font(30, True))
    canvas.save(MOCKUPS / "source-screen-strip-actual-800x480.png", quality=95)


def main() -> None:
    make_dirs()
    screens = copy_source_screens()
    make_hero(screens)
    make_feature_grid(screens)
    make_social_portrait(screens)
    make_marketplace_square(screens)
    make_screen_strip(screens)


if __name__ == "__main__":
    main()
