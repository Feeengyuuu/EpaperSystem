import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.svg_logo import MAX_SVG_BYTES, inline_classes, is_svg_url, render_svg  # noqa: E402


def test_class_rules_are_written_onto_their_elements_and_an_elements_own_style_wins():
    svg = ('<svg><style>.a, .b{fill:#fff;} /* note */ .c{stroke:red}</style>'
           '<path class="a" d="M0"/><rect class="b c" style="fill:#000"/><g class="none"/></svg>')

    out = inline_classes(svg)

    assert '<path class="a" d="M0" style="fill:#fff"/>' in out
    assert 'style="fill:#fff;stroke:red;fill:#000"' in out
    assert '<g class="none"/>' in out


def test_a_logo_draws_to_its_size_and_a_huge_or_empty_one_is_refused():
    logo = render_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 20 10"><rect width="20" height="10" '
                      b'fill="#123456"/></svg>', 40)

    assert logo.size == (40, 20) and logo.getpixel((20, 10)) == (18, 52, 86, 255)
    with pytest.raises(ValueError):
        render_svg(b" " * (MAX_SVG_BYTES + 1), 40)
    with pytest.raises(ValueError):
        render_svg(b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10"></svg>', 40)
    assert is_svg_url("https://img-cdn.hltv.org/teamlogo/a.svg?ixlib=java") and not is_svg_url("https://x/a.png?s=svg")
