import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.page_canvas import NO_LINE_START, PageCanvas, re_tokens  # noqa: E402

TITLE = "Youtu-Parsing-Omni：一个编码器、一套Schema，覆盖所有模态。"


def _canvas():
    return PageCanvas({"paper": (255, 255, 255), "ink": (0, 0, 0)})


def test_closing_punctuation_never_starts_a_line_and_a_word_moves_whole():
    canvas = _canvas()
    # From the width of the longest word (a narrower line cuts that word whatever the rule).
    for width in range(round(max(canvas.width(token.strip(), 13) for token in re_tokens(TITLE))) + 1, 300, 3):
        lines = canvas.wrap(TITLE, 13, width, 10)

        assert "".join(lines) == TITLE, width  # nothing lost or ellipsized
        assert not any(line[:1] in NO_LINE_START for line in lines[1:]), (width, lines)
        assert any("Schema" in line for line in lines), (width, lines)  # never split


def test_the_comma_takes_the_character_before_it_to_the_next_line():
    canvas = _canvas()
    width = canvas.width("一个编码器", 13) + 1  # the 、 after it does not fit

    assert canvas.wrap("一个编码器、一套模型", 13, width, 3) == ["一个编码", "器、一套模", "型"]
    # On the last line the text ellipsizes anyway, so nothing moves.
    assert canvas.wrap("一个编码器、一套模型", 13, width, 1)[0].startswith("一个编码")
