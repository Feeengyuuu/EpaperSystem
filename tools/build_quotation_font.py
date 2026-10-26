"""Build the brush hand BacktotheDate writes its Mao quotations in.

    PYTHONPATH=.tmp/fonttools-lib python tools/build_quotation_font.py <ZhiMangXing-Regular.ttf>

The source is Google Fonts' Zhi Mang Xing by Wei Zhimang (SIL Open Font License 1.1, google/fonts
ofl/zhimangxing), a running-script brush hand. It is kept to the characters of the quotations in
src/plugins/backtothedate/quotations.py and their punctuation, so a quotation added there needs
this run again. Writes src/plugins/backtothedate/assets/ZhiMangXing-Quotations.ttf and its
.source.json beside the licence.
"""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

from fontTools.subset import Options, Subsetter
from fontTools.ttLib import TTFont

REPO_ROOT = Path(__file__).resolve().parents[1]
PLUGIN = REPO_ROOT / "inkypi-weather" / "package" / "InkyPi" / "src" / "plugins" / "backtothedate"
TARGET = PLUGIN / "assets" / "ZhiMangXing-Quotations.ttf"
SOURCE_URL = "https://github.com/google/fonts/raw/main/ofl/zhimangxing/ZhiMangXing-Regular.ttf"
# Punctuation a quotation may carry besides its own, and the signature after every quotation.
EXTRA = "，。、；：？！“”‘’《》…—— 毛泽东"


def quotation_characters():
    """Every character of the quotations, read from the module's source without importing it."""
    tree = ast.parse((PLUGIN / "quotations.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(target, "id", "") == "QUOTATIONS" for target in node.targets):
            return "".join(text for text, _source in ast.literal_eval(node.value))
    raise SystemExit("QUOTATIONS not found")


def main(source):
    source = Path(source)
    font = TTFont(source)
    characters = sorted(set(quotation_characters() + EXTRA))
    cmap = font.getBestCmap()
    missing = [char for char in characters if ord(char) not in cmap and not char.isspace()]
    options = Options()
    options.layout_features = ["*"]
    options.name_IDs = ["*"]
    options.notdef_outline = True
    subsetter = Subsetter(options)
    subsetter.populate(unicodes=[ord(char) for char in characters])
    subsetter.subset(font)
    font.save(TARGET)
    record = {
        "font": TARGET.name, "source": SOURCE_URL, "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
        "designer": "Wei Zhimang", "licence": "SIL Open Font License 1.1 (ZhiMangXing-OFL.txt)",
        "characters": f"the {len(characters)} characters of quotations.py, their punctuation and the signature",
        "missing_from_source": "".join(missing),
        "sha256": hashlib.sha256(TARGET.read_bytes()).hexdigest(), "bytes": TARGET.stat().st_size,
    }
    TARGET.with_suffix(".source.json").write_text(json.dumps(record, ensure_ascii=False, indent=2) + "\n",
                                                  encoding="utf-8", newline="\n")
    print(json.dumps(record, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main(sys.argv[1])
