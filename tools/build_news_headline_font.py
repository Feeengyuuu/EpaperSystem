"""Build the serif headline face 整点新闻 sets its lead story in.

    PYTHONPATH=.tmp/fonttools-lib python tools/build_news_headline_font.py <NotoSerifSC[wght].ttf>

The source is Google Fonts' Noto Serif SC variable font (SIL Open Font License 1.1,
google/fonts ofl/notoserifsc). It is fixed at its heaviest weight (900), the weight of a
newspaper's banner headline, and kept to the characters a Chinese headline uses: the 6,763
hanzi of GB 2312, ASCII, Latin-1 and the general, CJK and full-width punctuation. A headline
with a character outside that set is drawn in the page's usual face instead. Writes
src/static/fonts/NotoSerifSC-Black-Headline.ttf and its .source.json beside the licence.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

REPO_ROOT = Path(__file__).resolve().parents[1]
FONTS = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "static" / "fonts"
TARGET = FONTS / "NotoSerifSC-Black-Headline.ttf"
SOURCE_URL = "https://github.com/google/fonts/raw/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"
WEIGHT = 900
PUNCTUATION_RANGES = ((0x20, 0x7E), (0xA0, 0xFF), (0x2000, 0x206F), (0x3000, 0x303F), (0xFF00, 0xFFEF))


def gb2312_hanzi():
    """The 6,763 hanzi of GB 2312 (rows 16-87)."""
    chars = []
    for row in range(0xB0, 0xF8):
        for cell in range(0xA1, 0xFF):
            try:
                chars.append(bytes((row, cell)).decode("gb2312"))
            except UnicodeDecodeError:
                continue
    return chars


def main(source):
    source = Path(source)
    font = TTFont(source)
    instantiateVariableFont(font, {"wght": WEIGHT}, inplace=True)
    codepoints = {ord(char) for char in gb2312_hanzi()}
    for low, high in PUNCTUATION_RANGES:
        codepoints.update(range(low, high + 1))
    options = Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=sorted(codepoints))
    subsetter.subset(font)
    font.save(TARGET)
    record = {
        "font": TARGET.name, "source": SOURCE_URL, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "licence": "SIL Open Font License 1.1 (NotoSerifSC-OFL.txt)", "weight": WEIGHT,
        "characters": "GB 2312 hanzi (6763), ASCII, Latin-1, general/CJK/full-width punctuation",
        "sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(), "bytes": TARGET.stat().st_size,
    }
    TARGET.with_suffix(".source.json").write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(json.dumps(record, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
