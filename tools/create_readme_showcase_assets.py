from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

try:
    import cv2
except Exception:  # pragma: no cover - keeps the script usable without OpenCV.
    cv2 = None


ROOT = Path(__file__).resolve().parents[1]
INKYPI = ROOT / "inkypi-weather" / "package" / "InkyPi"
OUT = INKYPI / "docs" / "images" / "readme"
BASES = OUT / "bases"
SCREENS = OUT / "screens"


@dataclass(frozen=True)
class Screen:
    slug: str
    label: str
    path: Path
    source: str


ACTUAL_SCREENS = [
    Screen("sports-dashboard", "SportsDashboard", SCREENS / "actual-sports-dashboard-800x480.png", "offline public sample render"),
    Screen("lol-info", "LoLInfo", SCREENS / "actual-lol-info-800x480.png", "/api/current_image after Display Now"),
    Screen("comic-covers", "ComicCovers", SCREENS / "actual-comic-covers-800x480.png", "/plugin_instance_image"),
    Screen("daily-ai-news", "Daily AI News", SCREENS / "actual-daily-ai-news-800x480.png", "/plugin_instance_image"),
    Screen("live-radar", "LiveRadar", SCREENS / "actual-live-radar-800x480.png", "/plugin_instance_image"),
    Screen("daily-art", "DailyArt", SCREENS / "actual-daily-art-800x480.png", "/plugin_instance_image"),
    Screen("box-office", "BoxOffice", SCREENS / "actual-box-office-800x480.png", "/plugin_instance_image"),
]


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


def ensure_inputs() -> None:
    missing = [screen.path for screen in ACTUAL_SCREENS if not screen.path.exists()]
    if missing:
        joined = "\n".join(str(path) for path in missing)
        raise FileNotFoundError(f"Missing actual device screenshots:\n{joined}")
    for base_name in ("img2-readme-hero-base.png", "img2-plugin-wall-base.png"):
        path = BASES / base_name
        if not path.exists():
            raise FileNotFoundError(path)


def order_quad(points: np.ndarray) -> np.ndarray:
    pts = np.asarray(points, dtype=np.float32)
    s = pts.sum(axis=1)
    diff = np.diff(pts, axis=1).reshape(-1)
    return np.array([pts[np.argmin(s)], pts[np.argmin(diff)], pts[np.argmax(s)], pts[np.argmax(diff)]], dtype=np.float32)


def expand_quad(points: np.ndarray, pixels: float = 7.0) -> np.ndarray:
    pts = order_quad(points)
    center = pts.mean(axis=0)
    vectors = pts - center
    lengths = np.linalg.norm(vectors, axis=1).reshape(4, 1)
    lengths[lengths == 0] = 1
    return pts + vectors / lengths * pixels


def green_components(image: Image.Image, min_area: int) -> list[np.ndarray]:
    arr = np.array(image.convert("RGB"))
    r, g, b = arr[:, :, 0], arr[:, :, 1], arr[:, :, 2]
    mask = ((g > 135) & (r < 115) & (b < 130) & (g > r * 1.45) & (g > b * 1.35)).astype(np.uint8)
    if cv2 is None:
        ys, xs = np.where(mask)
        if not len(xs):
            return []
        return [np.array([[xs.min(), ys.min()], [xs.max(), ys.min()], [xs.max(), ys.max()], [xs.min(), ys.max()]], dtype=np.float32)]

    kernel = np.ones((11, 11), np.uint8)
    clean = cv2.morphologyEx(mask * 255, cv2.MORPH_CLOSE, kernel)
    clean = cv2.dilate(clean, kernel, iterations=1)
    contours, _ = cv2.findContours(clean, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    quads: list[np.ndarray] = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < min_area:
            continue
        perimeter = cv2.arcLength(contour, True)
        approx = cv2.approxPolyDP(contour, 0.03 * perimeter, True)
        if len(approx) == 4:
            pts = approx.reshape(4, 2).astype(np.float32)
        else:
            rect = cv2.minAreaRect(contour)
            pts = cv2.boxPoints(rect).astype(np.float32)
        quads.append(expand_quad(pts))
    quads.sort(key=lambda pts: (float(pts[:, 1].mean()), float(pts[:, 0].mean())))
    return quads


def paste_screen_perspective(base: Image.Image, screen: Image.Image, quad: np.ndarray) -> Image.Image:
    screen = screen.convert("RGB").resize((800, 480), Image.Resampling.LANCZOS)
    if cv2 is None:
        x1, y1 = quad[:, 0].min(), quad[:, 1].min()
        x2, y2 = quad[:, 0].max(), quad[:, 1].max()
        resized = screen.resize((int(x2 - x1), int(y2 - y1)), Image.Resampling.LANCZOS)
        out = base.convert("RGBA")
        out.alpha_composite(resized.convert("RGBA"), (int(x1), int(y1)))
        return out

    src = np.array([[0, 0], [799, 0], [799, 479], [0, 479]], dtype=np.float32)
    dst = order_quad(quad)
    matrix = cv2.getPerspectiveTransform(src, dst)
    canvas_size = base.size
    warped = cv2.warpPerspective(np.array(screen), matrix, canvas_size)
    mask = cv2.warpPerspective(np.full((480, 800), 255, dtype=np.uint8), matrix, canvas_size)
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=1)
    out = np.array(base.convert("RGB"))
    out[mask > 2] = warped[mask > 2]
    return Image.fromarray(out).convert("RGBA")


def add_hero_copy(image: Image.Image) -> Image.Image:
    out = image.convert("RGBA")
    overlay = Image.new("RGBA", out.size, (0, 0, 0, 0))
    d = ImageDraw.Draw(overlay)
    d.rounded_rectangle((70, 92, 810, 560), radius=34, fill=(255, 255, 255, 225))
    d.text((112, 132), "EpaperSystem", font=font(70, True), fill=(20, 20, 20))
    d.text((116, 220), "Open-source Raspberry Pi\ne-paper dashboard", font=font(42, True), fill=(34, 34, 34), spacing=8)
    d.text((116, 342), "插件化墨水屏信息台\n一条命令安装，可选 API Key，中文流程", font=font(32), fill=(74, 74, 74), spacing=8)
    chips = ["800x480", "Waveshare", "Plugins", "zh-CN"]
    x = 116
    for chip in chips:
        box = d.textbbox((0, 0), chip, font=font(25, True))
        width = box[2] - box[0]
        d.rounded_rectangle((x, 475, x + width + 34, 522), radius=20, fill=(22, 22, 22, 245))
        d.text((x + 17, 484), chip, font=font(25, True), fill=(255, 255, 255))
        x += width + 48
    out.alpha_composite(overlay)
    return out.convert("RGB")


def compose_img2_hero(screens: list[Screen]) -> Path:
    with Image.open(BASES / "img2-readme-hero-base.png") as base:
        quads = green_components(base, min_area=80_000)
        if not quads:
            raise RuntimeError("No green screen placeholder found in hero base")
        with Image.open(screens[0].path) as screen_img:
            image = paste_screen_perspective(base, screen_img, quads[0])
    final = add_hero_copy(image)
    out = OUT / "epaper-system-hero.png"
    final.save(out, quality=95)
    return out


def compose_img2_plugin_wall(screens: list[Screen]) -> Path:
    with Image.open(BASES / "img2-plugin-wall-base.png") as base:
        quads = green_components(base, min_area=45_000)
        if len(quads) < 4:
            raise RuntimeError(f"Found {len(quads)} green placeholders in plugin wall, expected 4")
        image = base.convert("RGBA")
        for quad, screen in zip(quads[:4], screens[1:5]):
            with Image.open(screen.path) as screen_img:
                image = paste_screen_perspective(image, screen_img, quad)
    out = OUT / "epaper-system-plugin-wall.png"
    image.convert("RGB").save(out, quality=95)
    return out


def screenshot_grid(screens: list[Screen]) -> Path:
    width = 1920
    cols = 3
    rows = (len(screens) + cols - 1) // cols
    height = 215 + rows * 355 + 45
    canvas = Image.new("RGB", (width, height), (246, 247, 244))
    d = ImageDraw.Draw(canvas)
    d.text((70, 58), "Public sample render and device captures", font=font(50, True), fill=(18, 18, 18))
    d.text(
        (72, 122),
        "首页体育画面为公开样例数据离线渲染；其余画面来自已保存的 current_image / plugin_instance_image。",
        font=font(30),
        fill=(78, 78, 78),
    )

    thumb_w, thumb_h = 400, 240
    for idx, screen in enumerate(screens):
        col = idx % 3
        row = idx // 3
        x = 82 + col * 610
        y = 215 + row * 355
        d.rounded_rectangle(
            (x - 18, y - 18, x + thumb_w + 18, y + thumb_h + 82),
            radius=18,
            fill=(255, 255, 255),
            outline=(224, 224, 224),
            width=2,
        )
        with Image.open(screen.path) as source:
            thumb = source.convert("RGB").resize((thumb_w, thumb_h), Image.Resampling.LANCZOS)
        canvas.paste(thumb, (x, y))
        d.text((x, y + thumb_h + 22), screen.label, font=font(30, True), fill=(30, 30, 30))

    out = OUT / "epaper-system-real-screens.png"
    canvas.save(out, quality=95)
    return out


def write_manifest(screens: list[Screen], outputs: list[Path]) -> None:
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "device": "ColoredEpaperFrame",
        "device_host": "offline readme render; no live device contacted",
        "screens": [
            {
                "slug": item.slug,
                "label": item.label,
                "path": str(item.path.relative_to(INKYPI)),
                "source": item.source,
            }
            for item in screens
        ],
        "outputs": [str(path.relative_to(INKYPI)) for path in outputs],
        "rule": "img-2 bases contain only device/environment; SportsDashboard is an offline public sample render; other saved screens are existing device captures.",
    }
    (OUT / "manifest.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def main() -> None:
    ensure_inputs()
    screens = ACTUAL_SCREENS
    outputs = [compose_img2_hero(screens), compose_img2_plugin_wall(screens), screenshot_grid(screens)]
    write_manifest(screens, outputs)
    for path in outputs:
        print(path)


if __name__ == "__main__":
    main()
