"""Text that fills a box: wrapped at the largest size that fits, with one span in colour.

Shared by the pages that set a quote in a card of fixed size (时光书签, 晨读).
"""

from __future__ import annotations

from utils.page_canvas import NO_LINE_START, re_tokens


def rich_lines(canvas, text, size, width, max_lines, bold=False):
    """[(line, start offset)] of ``text`` (spaces collapsed) wrapped to ``width``: words break at
    spaces, CJK at characters, closing punctuation hangs on its line; None when it needs more lines."""
    text = " ".join(str(text or "").split())
    font = canvas.font(size, bold)
    lines, current, start, offset = [], "", 0, 0
    for token in re_tokens(text):
        if not current:
            stripped = token.lstrip()
            start, current = offset + len(token) - len(stripped), stripped
        elif (canvas.draw.textlength(current + token, font=font) <= width
              or token.lstrip()[:1] in NO_LINE_START):
            current += token
        else:
            lines.append((current, start))
            stripped = token.lstrip()
            start, current = offset + len(token) - len(stripped), stripped
            if len(lines) >= max_lines:
                return None
        offset += len(token)
    if current:
        lines.append((current, start))
    return lines if len(lines) <= max_lines else None


def fitted_lines(canvas, text, sizes, width, max_lines, bold=False):
    """(size, lines) at the largest of ``sizes`` that fits ``max_lines``; at the smallest the last
    line is cut with an ellipsis."""
    for size in sizes:
        lines = rich_lines(canvas, text, size, width, max_lines, bold)
        if lines is not None:
            return size, lines
    size = sizes[-1]
    lines = rich_lines(canvas, text, size, width, 10 ** 6, bold) or []
    kept = lines[:max_lines]
    if kept and len(lines) > max_lines:
        last, start = kept[-1]
        kept[-1] = (canvas.fit(last + "……", size, width, bold), start)
    return size, kept


def draw_rich(canvas, x, y, lines, size, line_height, span, colour, bold=False):
    """Draw ``lines`` from (x, y); the characters inside ``span`` (offsets into the text) take
    ``colour`` and the bold weight. Returns the y below the last line."""
    for line, start in lines:
        cursor = x
        cuts = sorted({0, len(line), *(max(0, min(len(line), edge - start)) for edge in (span or ()))})
        for begin, end in zip(cuts, cuts[1:]):
            piece = line[begin:end]
            lit = bool(span) and span[0] <= start + begin < span[1]
            font = canvas.font(size, bold or lit)
            canvas.draw.text((cursor, y), piece, font=font, fill=canvas.color(colour if lit else "ink"))
            cursor += canvas.draw.textlength(piece, font=font)
        y += line_height
    return y


def fit_box(canvas, text, sizes, width, height, ratio=1.4, bold=False):
    """(size, lines, line height) of ``text`` at the largest of ``sizes`` whose lines fit ``height``;
    at the smallest, as many lines as fit with the last cut by an ellipsis (none when not one fits)."""
    for size in sizes:
        line_height = round(size * ratio)
        lines = rich_lines(canvas, text, size, width, max(0, int(height // line_height)), bold)
        if lines:
            return size, lines, line_height
    size = sizes[-1]
    line_height = round(size * ratio)
    max_lines = int(height // line_height)
    if max_lines < 1:
        return size, [], line_height
    return size, fitted_lines(canvas, text, (size,), width, max_lines, bold)[1], line_height
