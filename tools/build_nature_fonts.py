"""Build the field-guide faces 湾区德鲁伊 (nature_nearby) sets its species in.

    PYTHONPATH=.tmp/fonttools-lib python tools/build_nature_fonts.py <NotoSerifSC[wght].ttf> <Lora-Italic[wght].ttf>

- Chinese names and headings: Google Fonts' Noto Serif SC variable font (SIL Open Font License
  1.1, google/fonts ofl/notoserifsc) fixed at SemiBold (600), the weight a printed field guide
  sets its species names in, kept to GB 2312's hanzi, ASCII, Latin-1 and the punctuation (as
  build_news_headline_font.py keeps the news headline face); a character outside it is drawn
  from the fallback faces.
- Scientific names: Lora Italic (SIL OFL 1.1, google/fonts ofl/lora), copied unchanged (its
  name is reserved), the variable font the page sets at its weight.

Writes src/static/fonts/NotoSerifSC-SemiBold-Nature.ttf, Lora-Italic-VF.ttf, Lora-OFL.txt and
NatureFonts.source.json.
"""

from __future__ import annotations

import hashlib
import json
import shutil
import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont
from fontTools.varLib.instancer import instantiateVariableFont

from build_news_headline_font import FONTS, PUNCTUATION_RANGES, gb2312_hanzi

SERIF_TARGET = FONTS / "NotoSerifSC-SemiBold-Nature.ttf"
ITALIC_TARGET = FONTS / "Lora-Italic-VF.ttf"
SERIF_SOURCE = "https://github.com/google/fonts/raw/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf"
ITALIC_SOURCE = "https://github.com/google/fonts/raw/main/ofl/lora/Lora-Italic%5Bwght%5D.ttf"
LICENCE_SOURCE = "https://github.com/google/fonts/raw/main/ofl/lora/OFL.txt"
SERIF_WEIGHT = 600


def _sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def main(serif_source, italic_source):
    serif_source, italic_source = Path(serif_source), Path(italic_source)
    font = TTFont(serif_source)
    instantiateVariableFont(font, {"wght": SERIF_WEIGHT}, inplace=True)
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
    font.save(SERIF_TARGET)
    shutil.copyfile(italic_source, ITALIC_TARGET)
    licence = italic_source.with_name("Lora-OFL.txt")
    if licence.is_file():
        shutil.copyfile(licence, FONTS / "Lora-OFL.txt")
    record = {
        SERIF_TARGET.name: {"source": SERIF_SOURCE, "source_sha256": _sha(serif_source),
                            "licence": "SIL Open Font License 1.1 (NotoSerifSC-OFL.txt)", "weight": SERIF_WEIGHT,
                            "characters": "GB 2312 hanzi (6763), ASCII, Latin-1, general/CJK/full-width punctuation",
                            "sha256": _sha(SERIF_TARGET), "bytes": SERIF_TARGET.stat().st_size},
        ITALIC_TARGET.name: {"source": ITALIC_SOURCE, "licence": f"SIL Open Font License 1.1 (Lora-OFL.txt, {LICENCE_SOURCE})",
                             "modified": "no (copied unchanged; Lora is a reserved font name)",
                             "sha256": _sha(ITALIC_TARGET), "bytes": ITALIC_TARGET.stat().st_size},
    }
    (FONTS / "NatureFonts.source.json").write_text(json.dumps(record, indent=2, ensure_ascii=False) + "\n",
                                                   encoding="utf-8", newline="\n")
    print(json.dumps(record, indent=1, ensure_ascii=False))


if __name__ == "__main__":
    main(*sys.argv[1:3])
