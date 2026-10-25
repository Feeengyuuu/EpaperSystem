"""Bundle airport logos (and their countries' flags) for Sky Radar's flight strips.

    python tools/build_sky_radar_airport_logos.py

For each IATA code below and in flight_radar's AIRPORT_CITY_ZH_CN, Wikidata gives the airport's
logo image (P154) and country (P17 -> ISO 3166 code). Logos come from Wikimedia Commons rendered
as PNG, trimmed to the mark with white made transparent where the logo sits on white; flags come
from flagcdn. Writes plugins/flight_radar/assets/route_airports/<IATA>.png, flags/<cc>.png and
airports.json ({iata: {country, logo}}), with the sources noted there.
"""

from __future__ import annotations

import io
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src"
OUT = SRC / "plugins" / "flight_radar" / "assets" / "route_airports"
USER_AGENT = "InkyPi asset build (https://github.com/Feeengyuuu/EpaperSystem)"
EXTRA = (
    "BOI MTY RSW ITO SBA SBP MRY FAT EUG RDM MFR ACV RDD SUN BZN JAC ASE EGE HDN MSO TUS ELP OKC TUL OMA DSM MKE "
    "CVG RDU TPA SJU BDL PVD MHT PWM BUF ROC SYR ALB ORF RIC CHS SAV JAX BHM MEM SDF LIT ICT GEG LGB BLI KTN JNU "
    "FAI YYC YEG YOW YLW SJD PVR MZT ZIH BJX MID SAL GUA LIR SJO LIM BOG GRU SCL EZE AKL BNE NAN KHH CTS FUK CAN "
    "CTU TFU SZX XMN SGN HAN BKK KUL CGK DPS DEL BOM BLR HYD AUH TLV AMS LGW MAN DUB VIE CPH ARN OSL HEL KEF LIS "
    "MXP ATH WAW YQR YXE YWG GCM NAS MBJ PUJ SXM AUA STT HAV CLO UIO MDE GYE ASU MVD POA REC FOR"
).split()
LOGO_WIDTH = 240
# Wordmarks whose letters the emblem crop would split: always kept whole.
KEEP_WHOLE = {"AMS", "YUL", "CPH", "SLC"}


def fetch(url, timeout=60):
    with urlopen(Request(url, headers={"User-Agent": USER_AGENT}), timeout=timeout) as response:
        return response.read()


def codes():
    sys.path.insert(0, str(SRC))
    from plugins.flight_radar.flight_radar import AIRPORT_CITY_ZH_CN

    return sorted(set(AIRPORT_CITY_ZH_CN) | set(EXTRA))


def wikidata(iata_codes):
    values = " ".join(f'"{code}"' for code in iata_codes)
    query = f"""SELECT ?iata ?logo ?cc WHERE {{
      VALUES ?iata {{ {values} }}
      ?airport wdt:P238 ?iata .
      FILTER NOT EXISTS {{ ?airport wdt:P576 ?closed }}
      OPTIONAL {{ ?airport wdt:P154 ?logo . }}
      OPTIONAL {{ ?airport wdt:P17 ?country . ?country wdt:P297 ?cc . }}
    }}"""
    url = "https://query.wikidata.org/sparql?" + urlencode({"query": query, "format": "json"})
    rows = json.loads(fetch(url, timeout=120))["results"]["bindings"]
    found = {}
    for row in rows:
        code = row["iata"]["value"]
        entry = found.setdefault(code, {"logo": "", "country": ""})
        if "logo" in row and not entry["logo"]:
            entry["logo"] = row["logo"]["value"]
        if "cc" in row and not entry["country"]:
            entry["country"] = row["cc"]["value"].lower()
    return found


def clean(image):
    """Trim to the mark; a logo drawn on white loses the white (only when its corners are white)."""
    image = image.convert("RGBA")
    corners = [image.getpixel(point) for point in ((0, 0), (image.width - 1, 0), (0, image.height - 1),
                                                     (image.width - 1, image.height - 1))]
    if all(alpha > 200 and min(rgb) > 240 for *rgb, alpha in corners):
        pixels = image.load()
        for y in range(image.height):
            for x in range(image.width):
                r, g, b, a = pixels[x, y]
                if r > 242 and g > 242 and b > 242:
                    pixels[x, y] = (255, 255, 255, 0)
    box = image.getchannel("A").point(lambda value: 255 if value > 12 else 0).getbbox()
    return image.crop(box) if box else None


def compact(image, code=""):
    """A long wordmark with a separate emblem at its left keeps just the emblem, which still reads at
    strip size; a compact logo stays whole."""
    width, height = image.size
    if code in KEEP_WHOLE or width / height <= 3.2:
        return image
    alpha = image.getchannel("A").point(lambda value: 255 if value > 24 else 0)
    columns = [alpha.crop((x, 0, x + 1, height)).getbbox() is not None for x in range(width)]
    gap = max(3, round(width * 0.025))
    start, empty = None, 0
    for x, filled in enumerate(columns + [False] * (gap + 1)):
        if filled:
            start = x if start is None else start
            empty, end = 0, x + 1
        elif start is not None:
            empty += 1
            if empty >= gap:
                break
    if start is None or end - start > width * 0.45:
        return image
    emblem = image.crop((start, 0, end, height))
    box = emblem.getchannel("A").point(lambda value: 255 if value > 24 else 0).getbbox()
    emblem = emblem.crop(box) if box else emblem
    return emblem if 0.5 <= emblem.width / emblem.height <= 2.6 else image


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "flags").mkdir(exist_ok=True)
    wanted = codes()
    found = {}
    for start in range(0, len(wanted), 80):
        found.update(wikidata(wanted[start:start + 80]))
        time.sleep(1)
    index = {}
    for code in wanted:
        entry = found.get(code) or {}
        record = {"country": entry.get("country", ""), "logo": ""}
        logo_url = entry.get("logo", "")
        if logo_url:
            name = logo_url.rsplit("/", 1)[-1]
            render = f"https://commons.wikimedia.org/wiki/Special:FilePath/{name}?width={LOGO_WIDTH}"
            try:
                image = clean(Image.open(io.BytesIO(fetch(render))))
            except Exception as error:  # noqa: BLE001 - a build script: report and move on
                print(code, "logo failed", type(error).__name__)
                image = None
            if image is not None and image.width >= 16:
                image = compact(image, code)
                image.thumbnail((LOGO_WIDTH, 96), Image.Resampling.LANCZOS)
                image.save(OUT / f"{code}.png", optimize=True)
                record["logo"] = "https://commons.wikimedia.org/wiki/File:" + quote(re.sub(r"%20", "_", name), safe="_%.()-")
            time.sleep(0.3)
        index[code] = record
    countries = sorted({record["country"] for record in index.values() if record["country"]})
    for country in countries:
        try:
            flag = Image.open(io.BytesIO(fetch(f"https://flagcdn.com/w40/{country}.png"))).convert("RGB")
        except Exception:  # noqa: BLE001
            continue
        flag.resize((max(1, round(flag.width * 16 / flag.height)), 16), Image.Resampling.LANCZOS).save(
            OUT / "flags" / f"{country}.png", optimize=True)
    (OUT / "airports.json").write_text(json.dumps({
        "sources": {"index": "Wikidata (P238 IATA, P154 logo, P17 country)", "logos": "Wikimedia Commons",
                    "flags": "flagcdn.com"},
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "airports": index,
    }, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n")
    with_logo = sum(1 for record in index.values() if record["logo"])
    print(json.dumps({"airports": len(index), "with_logo": with_logo, "countries": len(countries)}))


if __name__ == "__main__":
    main()
