"""A compact almanac strip beneath the date, independent of the event and host."""


def _whole_items(draw, items, font, width):
    """Fit complete almanac entries in their original order."""
    shown = []
    for item in items:
        candidate = " ".join([*shown, str(item)])
        if draw.textlength(candidate, font=font) > width:
            break
        shown.append(str(item))
    return " ".join(shown)


def draw_strip(draw, box, note, *, fonts, ink, paper):
    """Draw within ``box`` when it fits; return its bounds, or None on a small panel.

    The page retains its full month heading beside the date panel. This strip takes
    only the decorative underline and duplicate month caption's area, leaving the
    main date, today's event, host stage and monthly agenda untouched.
    """
    left, top, right, bottom = box
    if not note or right - left < 240 or bottom - top < 66:
        return None
    head, body, small = fonts
    draw.rectangle(box, fill=paper)
    draw.text((left + 7, top + 9), "黄历", font=head, fill=ink, anchor="lm")
    draw.text((right - 7, top + 9), note.get("ganzhi", ""), font=small, fill=ink, anchor="rm")
    for offset, label, key in ((27, "宜", "suit"), (44, "忌", "avoid")):
        draw.text((left + 7, top + offset), label, font=head, fill=ink, anchor="lm")
        text = _whole_items(draw, note.get(key) or [], body, right - left - 40)
        draw.text((left + 30, top + offset), text, font=body, fill=ink, anchor="lm")
    term = str(note.get("term") or "")
    if draw.textlength(term, font=small) <= right - left - 14:
        draw.text((left + 7, top + 60), term, font=small, fill=ink, anchor="lm")
    return box
