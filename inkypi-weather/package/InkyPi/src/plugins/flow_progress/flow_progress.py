from plugins.base_plugin.base_plugin import BasePlugin
from PIL import Image, ImageColor, ImageDraw
from utils.app_utils import get_font
import calendar
import unicodedata
from datetime import datetime, timedelta
import pytz

DEFAULT_NUM_BARS = 2
DEFAULT_NUM_DOTS = 20

# Color tokens follow docs/color-ui-guidelines.md: process black linework,
# paper ground, and vintage comic process-color accents.
COMIC_DAY_PAPER = (255, 248, 220)  # 25Y PANTONE 100, panel-calibrated later
COMIC_DAY_PANEL = (255, 253, 240)
COMIC_DAY_INK = (8, 8, 8)  # PROCESS BLACK
COMIC_DAY_MUTED = (168, 158, 130)  # 50Y-25R-25B PANTONE 465 family
COMIC_DAY_YELLOW = (255, 196, 30)  # 100Y-25R PANTONE 123 family
COMIC_DAY_ORANGE = (245, 122, 38)  # 100Y-50R PANTONE ORANGE 021 family
COMIC_DAY_BLUE = (0, 92, 185)  # 100B-25R PANTONE 285 family
COMIC_DAY_CATEGORY_STYLES = [
    {
        "key": "day",
        "label": "DAY",
        "color": (222, 45, 38),  # 100Y-100R PANTONE RED 032 family
    },
    {
        "key": "week",
        "label": "WEEK",
        "color": COMIC_DAY_BLUE,
    },
    {
        "key": "month",
        "label": "MONTH",
        "color": COMIC_DAY_YELLOW,
    },
    {
        "key": "year",
        "label": "YEAR",
        "color": (0, 152, 82),  # 100Y-100B PANTONE 354 family
    },
]
LEGACY_DARK_DEFAULTS = {
    ("#ffffff", "#000000"),
    ("#fff", "#000"),
}

# Hardcoded locale data for languages that render cleanly in Dogica.
# Text stays uppercase, preserving accents that are known to render correctly.
# Languages where transliteration would produce poor results remain excluded.
LOCALE_DATA = {
    "de": {
        "days": ["MONTAG", "DIENSTAG", "MITTWOCH", "DONNERSTAG", "FREITAG", "SAMSTAG", "SONNTAG"],
        "months": ["JANUAR", "FEBRUAR", "MÄRZ", "APRIL", "MAI", "JUNI",
                   "JULI", "AUGUST", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DEZEMBER"],
        "week": "WOCHE",
    },
    "en": None,  # English uses strftime directly
    "es": {
        "days": ["LUNES", "MARTES", "MIÉRCOLES", "JUEVES", "VIERNES", "SÁBADO", "DOMINGO"],
        "months": ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO",
                   "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"],
        "week": "SEMANA",
    },
    "fr": {
        "days": ["LUNDI", "MARDI", "MERCREDI", "JEUDI", "VENDREDI", "SAMEDI", "DIMANCHE"],
        "months": ["JANVIER", "FÉVRIER", "MARS", "AVRIL", "MAI", "JUIN",
                   "JUILLET", "AOÛT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DÉCEMBRE"],
        "week": "SEMAINE",
    },
    "id": {
        "days": ["SENIN", "SELASA", "RABU", "KAMIS", "JUMAT", "SABTU", "MINGGU"],
        "months": ["JANUARI", "FEBRUARI", "MARET", "APRIL", "MEI", "JUNI",
                   "JULI", "AGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DESEMBER"],
        "week": "MINGGU KE",
    },
    "it": {
        "days": ["LUNEDÌ", "MARTEDÌ", "MERCOLEDÌ", "GIOVEDÌ", "VENERDÌ", "SABATO", "DOMENICA"],
        "months": ["GENNAIO", "FEBBRAIO", "MARZO", "APRILE", "MAGGIO", "GIUGNO",
                   "LUGLIO", "AGOSTO", "SETTEMBRE", "OTTOBRE", "NOVEMBRE", "DICEMBRE"],
        "week": "SETTIMANA",
    },
    "nl": {
        "days": ["MAANDAG", "DINSDAG", "WOENSDAG", "DONDERDAG", "VRIJDAG", "ZATERDAG", "ZONDAG"],
        "months": ["JANUARI", "FEBRUARI", "MAART", "APRIL", "MEI", "JUNI",
                   "JULI", "AUGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DECEMBER"],
        "week": "WEEK",
    },
    "pt": {
        "days": ["SEGUNDA", "TERÇA", "QUARTA", "QUINTA", "SEXTA", "SÁBADO", "DOMINGO"],
        "months": ["JANEIRO", "FEVEREIRO", "MARÇO", "ABRIL", "MAIO", "JUNHO",
                   "JULHO", "AGOSTO", "SETEMBRO", "OUTUBRO", "NOVEMBRO", "DEZEMBRO"],
        "week": "SEMANA",
    },
}


def _localize(naive_dt, tz):
    """Localize a naive datetime safely for both pytz and stdlib tzinfo."""
    try:
        return tz.localize(naive_dt)
    except AttributeError:
        return naive_dt.replace(tzinfo=tz)


def calc_day_progress(dt):
    if dt.tzinfo is None:
        elapsed = dt.hour * 3600 + dt.minute * 60 + dt.second
        return min(max(elapsed / 86400, 0.0), 1.0)
    tz = dt.tzinfo
    start = _localize(datetime(dt.year, dt.month, dt.day), tz)
    tomorrow = dt.date() + timedelta(days=1)
    end = _localize(datetime(tomorrow.year, tomorrow.month, tomorrow.day), tz)
    total = (end - start).total_seconds()
    elapsed = (dt - start).total_seconds()
    return min(max(elapsed / total, 0.0), 1.0)


def calc_week_progress(dt):
    return min(max((dt.weekday() + 1) / 7, 0.0), 1.0)


def calc_month_progress(dt):
    days_in_month = calendar.monthrange(dt.year, dt.month)[1]
    return min(max(dt.day / days_in_month, 0.0), 1.0)


def calc_year_progress(dt):
    total_days = 366 if calendar.isleap(dt.year) else 365
    return min(max(dt.timetuple().tm_yday / total_days, 0.0), 1.0)

def get_labels(dt, language):
    locale = LOCALE_DATA.get(language)
    if locale:
        return [
            locale["days"][dt.weekday()],
            f"{locale['week']} {dt.isocalendar()[1]}",
            locale["months"][dt.month - 1],
            str(dt.year),
        ]
    # English (default) keeps strftime and strips accents only as a safe fallback.
    return [
        _strip_accents(dt.strftime("%A").upper()),
        f"WEEK {dt.isocalendar()[1]}",
        _strip_accents(dt.strftime("%B").upper()),
        str(dt.year),
    ]

def _strip_accents(text):
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if not unicodedata.combining(c))

def _parse_rgb(value):
    if value is None:
        return None
    try:
        rgb = ImageColor.getrgb(str(value))
    except (TypeError, ValueError):
        return None
    return tuple(int(channel) for channel in rgb[:3])

def _normalize_color(value):
    rgb = _parse_rgb(value)
    if rgb is None:
        return None
    return "#{:02x}{:02x}{:02x}".format(*rgb)

def _blend_rgb(fg, bg, amount):
    amount = min(max(amount, 0.0), 1.0)
    return tuple(int(round(f * amount + b * (1.0 - amount))) for f, b in zip(fg, bg))

def _luma(rgb):
    r, g, b = rgb
    return 0.2126 * r + 0.7152 * g + 0.0722 * b

def _comic_day_colors(settings):
    primary_hex = _normalize_color(settings.get("primaryColor"))
    secondary_hex = _normalize_color(settings.get("secondaryColor"))
    legacy_pair = (
        (primary_hex or "#ffffff"),
        (secondary_hex or "#000000"),
    ) in LEGACY_DARK_DEFAULTS

    paper = COMIC_DAY_PAPER
    if secondary_hex and not legacy_pair:
        paper = _parse_rgb(secondary_hex) or COMIC_DAY_PAPER

    ink = COMIC_DAY_INK
    primary = _parse_rgb(primary_hex)
    if primary and primary_hex != "#ffffff" and _luma(primary) < 110:
        ink = primary

    panel = _blend_rgb(COMIC_DAY_PANEL, paper, 0.64)
    return {
        "paper": paper,
        "panel": panel,
        "ink": ink,
        "muted": COMIC_DAY_MUTED,
        "yellow": COMIC_DAY_YELLOW,
        "orange": COMIC_DAY_ORANGE,
        "blue": COMIC_DAY_BLUE,
    }

def _draw_dot(draw, cx, cy, radius, fill, outline=None, outline_width=0):
    if outline and outline_width > 0:
        outer = radius + outline_width
        draw.ellipse(
            [cx - outer, cy - outer, cx + outer, cy + outer],
            fill=outline,
        )
    draw.ellipse(
        [cx - radius, cy - radius, cx + radius, cy + radius],
        fill=fill,
    )

def _draw_polygon_with_outline(draw, points, fill, outline, width):
    draw.polygon(points, fill=fill)
    draw.line(points + [points[0]], fill=outline, width=width)

def _text_size(draw, text, font):
    bbox = draw.textbbox((0, 0), text, font=font)
    return bbox[2] - bbox[0], bbox[3] - bbox[1]

def _draw_centered_text(draw, box, text, font, fill, stroke_fill=None, stroke_width=0):
    x0, y0, x1, y1 = box
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    x = x0 + (x1 - x0 - tw) / 2 - bbox[0]
    y = y0 + (y1 - y0 - th) / 2 - bbox[1]
    draw.text(
        (x, y),
        text,
        font=font,
        fill=fill,
        stroke_fill=stroke_fill,
        stroke_width=stroke_width,
    )

def _draw_halftone(draw, bounds, color, paper, spacing, radius):
    x0, y0, x1, y1 = [int(v) for v in bounds]
    dot_color = _blend_rgb(color, paper, 0.28)
    for y in range(y0, y1, spacing):
        for x in range(x0, x1, spacing):
            offset = (spacing // 2) if ((y // spacing) % 2) else 0
            cx = x + offset
            if x0 <= cx <= x1:
                draw.ellipse(
                    [cx - radius, y - radius, cx + radius, y + radius],
                    fill=dot_color,
                )

def _draw_comic_panel(draw, box, fill, outline, shadow, border_width, shadow_offset):
    x0, y0, x1, y1 = [int(v) for v in box]
    draw.rectangle(
        [x0 + shadow_offset, y0 + shadow_offset, x1 + shadow_offset, y1 + shadow_offset],
        fill=shadow,
    )
    draw.rectangle([x0, y0, x1, y1], fill=fill)
    draw.rectangle([x0, y0, x1, y1], outline=outline, width=border_width)

def text_to_dots(text, font):
    buf_w = max(len(text) * 20, 200)
    temp = Image.new("L", (buf_w, 50), 0)
    ImageDraw.Draw(temp).text((1, 1), text, font=font, fill=255)
    bbox = temp.getbbox()
    if not bbox:
        return [], 0, 0
    cropped = temp.crop(bbox)
    w, h = cropped.size
    px = cropped.load()
    dots = [(x, y) for y in range(h) for x in range(w) if px[x, y] > 128]
    return dots, w, h

def render_dots(
    draw,
    dots,
    x,
    y,
    spacing,
    radius,
    color,
    outline=None,
    outline_width=0,
    shadow=None,
    shadow_offset=0,
):
    for dx, dy in dots:
        cx = x + dx * spacing
        cy = y + dy * spacing
        if shadow:
            _draw_dot(
                draw,
                cx + shadow_offset,
                cy + shadow_offset,
                radius,
                shadow,
            )
        _draw_dot(draw, cx, cy, radius, color, outline, outline_width)

class FlowProgress(BasePlugin):
    def generate_settings_template(self):
        template_params = super().generate_settings_template()
        template_params['style_settings'] = False
        return template_params

    def generate_image(self, settings, device_config):
        dimensions = self.get_dimensions(device_config)

        width, height = dimensions
        
        # Safely extract and validate settings
        language = str(settings.get("language", "en")).strip() or "en"
        
        try:
            num_dots = int(settings.get("numDots", DEFAULT_NUM_DOTS))
            num_dots = max(5, min(40, num_dots))
        except (TypeError, ValueError):
            num_dots = DEFAULT_NUM_DOTS
        
        try:
            num_bars = int(settings.get("numBars", DEFAULT_NUM_BARS))
            num_bars = max(1, min(3, num_bars))
        except (TypeError, ValueError):
            num_bars = DEFAULT_NUM_BARS
        
        colors = _comic_day_colors(settings)
        PAPER = colors["paper"]
        PANEL = colors["panel"]
        INK = colors["ink"]
        MUTED = colors["muted"]
        YELLOW = colors["yellow"]
        ORANGE = colors["orange"]
        BLUE = colors["blue"]

        tz_name = device_config.get_config("timezone", default="UTC") or "UTC"
        try:
            tz = pytz.timezone(tz_name)
        except pytz.exceptions.UnknownTimeZoneError:
            tz = pytz.UTC
        now = datetime.now(tz)
        labels = get_labels(now, language)
        progresses = [
            calc_day_progress(now),
            calc_week_progress(now),
            calc_month_progress(now),
            calc_year_progress(now),
        ]
        pcts = [min(max(round(p * 100), 0), 100) for p in progresses]
        filled_counts = [min(max(round(p * num_dots), 0), num_dots) for p in progresses]
        scale = 2
        rw, rh = width * scale, height * scale
        img = Image.new("RGB", (rw, rh), PAPER)
        draw = ImageDraw.Draw(img)
        dot_font = get_font("Dogica", 8, font_weight="bold") or get_font("Dogica", 8)
        if dot_font is None:
            raise RuntimeError("Required font 'Dogica' not found.")
        title_font = (
            get_font("Jost", max(30, int(rw * 0.055)), font_weight="bold")
            or dot_font
        )

        pad_x = max(int(rw * 0.035), 24 * scale)
        pad_y = max(int(rh * 0.040), 18 * scale)
        border_w = max(3 * scale, 4)
        shadow_offset = max(4 * scale, int(rw * 0.006))
        draw.rectangle(
            [border_w // 2, border_w // 2, rw - border_w // 2 - 1, rh - border_w // 2 - 1],
            outline=INK,
            width=border_w,
        )
        _draw_halftone(
            draw,
            (rw - int(rw * 0.28), pad_y // 3, rw - pad_x, pad_y + int(rh * 0.13)),
            BLUE,
            PAPER,
            max(11 * scale, int(rw * 0.018)),
            max(2 * scale, int(rw * 0.004)),
        )
        _draw_halftone(
            draw,
            (pad_x // 2, rh - pad_y - int(rh * 0.16), int(rw * 0.30), rh - pad_y // 2),
            COMIC_DAY_CATEGORY_STYLES[0]["color"],
            PAPER,
            max(12 * scale, int(rw * 0.019)),
            max(2 * scale, int(rw * 0.004)),
        )

        title_h = max(int(rh * 0.105), 50 * scale)
        title_w = min(int(rw * 0.72), 630 * scale)
        title_x = pad_x
        title_y = pad_y
        slant = max(9 * scale, int(title_w * 0.035))
        title_text_pad = max(18 * scale, int(title_w * 0.035))
        title_poly = [
            (title_x + slant, title_y),
            (title_x + title_w, title_y),
            (title_x + title_w - slant, title_y + title_h),
            (title_x, title_y + title_h),
        ]
        shadow_poly = [(x + shadow_offset, y + shadow_offset) for x, y in title_poly]
        _draw_polygon_with_outline(draw, shadow_poly, COMIC_DAY_CATEGORY_STYLES[0]["color"], INK, border_w)
        _draw_polygon_with_outline(draw, title_poly, YELLOW, INK, border_w)
        _draw_centered_text(
            draw,
            (
                title_x + slant + title_text_pad,
                title_y,
                title_x + title_w - slant - title_text_pad,
                title_y + title_h,
            ),
            f"PROGRESSION of {now.year}",
            title_font,
            INK,
        )

        meta_w = min(int(rw * 0.28), 220 * scale)
        meta_h = int(title_h * 0.64)
        meta_box = (
            rw - pad_x - meta_w,
            title_y + int(title_h * 0.18),
            rw - pad_x,
            title_y + int(title_h * 0.18) + meta_h,
        )
        if meta_box[0] > title_x + title_w + 12 * scale:
            meta_font = get_font("Jost", max(15 * scale, int(meta_h * 0.42)), font_weight="bold") or dot_font
            _draw_comic_panel(draw, meta_box, BLUE, INK, ORANGE, border_w, shadow_offset // 2)
            _draw_centered_text(
                draw,
                (meta_box[0] + 5 * scale, meta_box[1], meta_box[2] - 5 * scale, meta_box[3]),
                "DAY MODE",
                meta_font,
                PAPER,
                stroke_fill=INK,
                stroke_width=max(1, scale // 2),
            )

        body_top = title_y + title_h + max(int(rh * 0.035), 16 * scale)
        body_bottom = rh - pad_y
        row_gap = max(int(rh * 0.020), 9 * scale)
        content_h = body_bottom - body_top - row_gap * (len(labels) - 1)
        item_count = len(labels)
        row_h = content_h / item_count
        content_gap = max(int(rw * 0.020), 16 * scale)
        tag_w = min(max(int(rw * 0.13), 78 * scale), 116 * scale)
        label_info = [text_to_dots(label, dot_font) for label in labels]
        pct_info = [text_to_dots(f"{pct}%", dot_font) for pct in pcts]
        max_label_pw = max((d[1] for d in label_info), default=1) or 1
        max_pct_pw = max((d[1] for d in pct_info), default=1) or 1
        max_ph = max(
            max((d[2] for d in label_info), default=7),
            max((d[2] for d in pct_info), default=7),
        ) or 7
        vertical_spacing = (row_h * 0.26) / max_ph
        min_bar_w = rw * 0.26
        text_bar_space = rw - 2 * pad_x - tag_w - min_bar_w - content_gap * 4
        width_spacing = text_bar_space / max(max_label_pw + max_pct_pw, 1)
        dot_spacing = max(min(vertical_spacing, width_spacing), 1.6 * scale)
        dot_radius = max(dot_spacing * 0.40, 0.7 * scale)
        max_lw = max_label_pw * dot_spacing
        max_pw = max_pct_pw * dot_spacing
        text_h = max_ph * dot_spacing
        label_x = pad_x + tag_w + content_gap
        bar_start = label_x + max_lw + content_gap
        bar_end = rw - pad_x - max_pw - content_gap * 2
        bar_width = bar_end - bar_start
        if bar_width < min_bar_w:
            bar_width = min_bar_w
            bar_start = max(label_x + max_lw + content_gap, rw - pad_x - max_pw - content_gap * 2 - bar_width)
            bar_end = bar_start + bar_width
        bar_dot_sp = bar_width / max(num_dots, 1)
        bar_block_h = row_h * 0.45
        bar_gap = max(row_h * 0.07, 5 * scale)
        available_bar_h = max(bar_block_h - bar_gap * (num_bars - 1), row_h * 0.16)
        single_bar_h = available_bar_h / num_bars
        bar_dot_r = min(bar_dot_sp * 0.34, single_bar_h * 0.42, 7.5 * scale)
        dot_outline = max(1.0, 0.55 * scale)
        text_shadow = max(0.9 * scale, 1.0)
        tag_font = get_font("Jost", max(15 * scale, int(row_h * 0.20)), font_weight="bold") or dot_font

        for i in range(item_count):
            style = COMIC_DAY_CATEGORY_STYLES[i % len(COMIC_DAY_CATEGORY_STYLES)]
            accent = style["color"]
            dim = _blend_rgb(accent, PAPER, 0.24)
            row_y0 = body_top + i * (row_h + row_gap)
            row_y1 = row_y0 + row_h
            panel_box = (pad_x, row_y0, rw - pad_x, row_y1)
            _draw_comic_panel(draw, panel_box, PANEL, INK, ORANGE, border_w, shadow_offset // 2)
            _draw_halftone(
                draw,
                (rw - pad_x - row_h * 1.25, row_y0 + border_w, rw - pad_x - border_w, row_y1 - border_w),
                accent,
                PANEL,
                max(9 * scale, int(row_h * 0.12)),
                max(1 * scale, int(row_h * 0.018)),
            )
            tag_box = (pad_x, row_y0, pad_x + tag_w, row_y1)
            draw.rectangle(tag_box, fill=accent)
            draw.rectangle(tag_box, outline=INK, width=border_w)
            tag_fill = INK if _luma(accent) > 150 else PAPER
            _draw_centered_text(
                draw,
                (
                    tag_box[0] + 4 * scale,
                    tag_box[1],
                    tag_box[2] - 4 * scale,
                    tag_box[3],
                ),
                style["label"],
                tag_font,
                tag_fill,
                stroke_fill=INK if tag_fill == PAPER else None,
                stroke_width=scale if tag_fill == PAPER else 0,
            )

            cy = row_y0 + row_h / 2
            ty = cy - text_h / 2
            l_dots, _, _ = label_info[i]
            render_dots(
                draw,
                l_dots,
                label_x,
                ty,
                dot_spacing,
                dot_radius,
                accent,
                shadow=INK,
                shadow_offset=text_shadow,
            )
            filled = filled_counts[i]
            bars_total_h = num_bars * (bar_dot_r * 2) + (num_bars - 1) * bar_gap
            top_y = cy - bars_total_h / 2 + bar_dot_r
            for bar_index in range(num_bars):
                bar_y = top_y + bar_index * (bar_dot_r * 2 + bar_gap)
                for j in range(num_dots):
                    cx = bar_start + j * bar_dot_sp + bar_dot_sp / 2
                    c = accent if j < filled else dim
                    outline = INK if j < filled else MUTED
                    _draw_dot(
                        draw,
                        cx,
                        bar_y,
                        bar_dot_r,
                        c,
                        outline=outline,
                        outline_width=dot_outline,
                    )
            p_dots, p_pw, _ = pct_info[i]
            px = rw - pad_x - content_gap - p_pw * dot_spacing
            render_dots(
                draw,
                p_dots,
                px,
                ty,
                dot_spacing,
                dot_radius,
                INK,
                shadow=accent,
                shadow_offset=text_shadow,
            )
        img = img.resize(dimensions, Image.LANCZOS)
        return img
