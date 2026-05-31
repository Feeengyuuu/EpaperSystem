from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont


ROOT = Path(__file__).resolve().parents[1]
GEN = Path("G:/PersonalProjects/AIWriter/.codex/generated_images/019e6d92-39c8-77c0-916f-980944c4235c")
BASES = ROOT / "marketing_assets" / "img2_bases"
OUT = ROOT / "marketing_assets" / "taobao_img2"
SCREENS = ROOT / "marketing_assets" / "source_screens"


@dataclass(frozen=True)
class BaseSpec:
    slug: str
    source: Path
    screens: tuple[str, ...]


BASE_SPECS = [
    BaseSpec(
        "main",
        GEN / "ig_03cc43d961ad71fb016a17f912e44c81978270309f16eef83a.png",
        ("weather",),
    ),
    BaseSpec(
        "feature",
        GEN / "ig_03cc43d961ad71fb016a17f98286bc819789450eb119be5f73.png",
        ("weather", "calendar", "pet"),
    ),
    BaseSpec(
        "kit",
        GEN / "ig_03cc43d961ad71fb016a17fa4cda24819783aed9c41c15958c.png",
        ("calendar",),
    ),
]


def ensure_dirs() -> None:
    BASES.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
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


def text_box(draw: ImageDraw.ImageDraw, text: str, fnt: ImageFont.ImageFont) -> tuple[int, int]:
    box = draw.textbbox((0, 0), text, font=fnt)
    return box[2] - box[0], box[3] - box[1]


def screen_path(slug: str) -> Path:
    path = SCREENS / f"{slug}-800x480.png"
    if not path.exists():
        raise FileNotFoundError(path)
    with Image.open(path) as im:
        if im.size != (800, 480):
            raise ValueError(f"{path} is {im.size}, expected 800x480")
    return path


def green_boxes(img: Image.Image, expected: int) -> list[tuple[int, int, int, int]]:
    rgb = np.array(img.convert("RGB"))
    r, g, b = rgb[:, :, 0], rgb[:, :, 1], rgb[:, :, 2]
    mask = ((g > 150) & (r < 90) & (b < 110) & (g > r * 1.8) & (g > b * 1.8)).astype(np.uint8) * 255
    kernel = np.ones((7, 7), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel)
    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    boxes: list[tuple[int, int, int, int]] = []
    for contour in contours:
        x, y, w, h = cv2.boundingRect(contour)
        area = w * h
        ratio = w / max(1, h)
        if area < 20_000:
            continue
        if not 1.35 <= ratio <= 2.25:
            continue
        boxes.append((x, y, x + w, y + h))

    boxes.sort(key=lambda box: (box[1], box[0]))
    if len(boxes) < expected:
        raise RuntimeError(f"Found {len(boxes)} green screen boxes, expected {expected}")
    return boxes[:expected]


def paste_real_screens(img: Image.Image, boxes: list[tuple[int, int, int, int]], screen_slugs: tuple[str, ...]) -> Image.Image:
    out = img.convert("RGBA")
    for box, slug in zip(boxes, screen_slugs):
        x1, y1, x2, y2 = box
        # Cover the full detected green placeholder. A visible green edge would imply
        # the model-generated placeholder survived into the final product image.
        target_w = x2 - x1
        target_h = y2 - y1
        with Image.open(screen_path(slug)) as screen:
            resized = screen.convert("RGB").resize((target_w, target_h), Image.Resampling.LANCZOS)
        out.alpha_composite(resized.convert("RGBA"), (x1, y1))
    return out


def rounded_label(draw: ImageDraw.ImageDraw, xy: tuple[int, int], text: str, fill: tuple[int, int, int], fg: tuple[int, int, int], size: int, bold: bool = True) -> int:
    x, y = xy
    fnt = font(size, bold)
    tw, th = text_box(draw, text, fnt)
    draw.rounded_rectangle((x, y, x + tw + size, y + th + size * 0.65), radius=size // 2, fill=fill)
    draw.text((x + size // 2, y + size // 4), text, fill=fg, font=fnt)
    return x + tw + size + 14


def draw_taobao_main(img: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    d = ImageDraw.Draw(out)
    d.text((76, 84), "7.5英寸墨水屏信息台", fill=(255, 255, 255), font=font(70, True), stroke_width=2, stroke_fill=(160, 0, 0))
    d.text((82, 168), "真实800×480横屏显示", fill=(255, 244, 190), font=font(48, True), stroke_width=2, stroke_fill=(150, 0, 0))
    d.rounded_rectangle((78, 240, 600, 306), radius=30, fill=(255, 255, 255, 235))
    d.text((112, 251), "天气 / 日历 / 新闻 / AI宠物 / 游戏状态", fill=(210, 38, 0), font=font(27, True))
    x = 80
    for tag in ["无前置按钮", "自动轮换", "桌面常亮"]:
        x = rounded_label(d, (x, 336), tag, (20, 20, 20), (255, 255, 255), 26)
    d.rounded_rectangle((82, 1120, 696, 1192), radius=34, fill=(255, 235, 0, 245))
    d.text((124, 1133), "屏幕内容来自项目实际截图", fill=(180, 20, 0), font=font(33, True))
    return out


def draw_taobao_feature(img: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    d = ImageDraw.Draw(out)
    d.text((86, 44), "多场景自动轮换  一屏掌握日常信息", fill=(210, 20, 0), font=font(62, True))
    d.text((92, 122), "屏幕均为真实800×480横向截图，非生成式UI", fill=(80, 80, 80), font=font(33, True))
    labels = [("天气预报", 140, 815), ("日历日程", 662, 815), ("AI桌宠", 1184, 815)]
    for text, x, y in labels:
        d.rounded_rectangle((x, y, x + 360, y + 62), radius=30, fill=(255, 242, 205, 245), outline=(248, 98, 0), width=3)
        d.text((x + 68, y + 12), text, fill=(210, 24, 0), font=font(33, True))
    return out


def draw_taobao_kit(img: Image.Image) -> Image.Image:
    out = img.convert("RGBA")
    d = ImageDraw.Draw(out)
    d.text((60, 58), "桌面常亮信息窗口", fill=(255, 255, 255), font=font(68, True), stroke_width=3, stroke_fill=(165, 0, 0))
    d.text((66, 138), "横向800×480真实显示 · 到手即用套装", fill=(255, 244, 190), font=font(39, True), stroke_width=2, stroke_fill=(165, 0, 0))
    d.rounded_rectangle((68, 1002, 660, 1080), radius=38, fill=(255, 235, 0, 245))
    d.text((108, 1018), "含设备框 / 支架 / USB-C线 / 快速上手卡", fill=(185, 24, 0), font=font(29, True))
    return out


def write_outputs() -> None:
    ensure_dirs()
    for spec in BASE_SPECS:
        if not spec.source.exists():
            raise FileNotFoundError(spec.source)
        base_dest = BASES / f"taobao-{spec.slug}-img2-base.png"
        shutil.copy2(spec.source, base_dest)
        with Image.open(base_dest) as base:
            boxes = green_boxes(base, len(spec.screens))
            composited = paste_real_screens(base, boxes, spec.screens)
        if spec.slug == "main":
            final = draw_taobao_main(composited)
        elif spec.slug == "feature":
            final = draw_taobao_feature(composited)
        elif spec.slug == "kit":
            final = draw_taobao_kit(composited)
        else:
            final = composited
        final = final.convert("RGB")
        final.save(OUT / f"taobao-{spec.slug}-img2-real-screen.png", quality=95)


if __name__ == "__main__":
    write_outputs()
