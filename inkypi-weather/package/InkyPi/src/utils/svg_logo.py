"""Small SVG logos (a team's, as a provider serves it) drawn to an RGBA image by PyMuPDF.

PyMuPDF draws an SVG's presentation attributes but not the rules its <style> block gives
classes, which leaves such a logo all black (HLTV's TYLOO, MOUZ); those rules are written onto
the elements first.
"""

from __future__ import annotations

import re

from PIL import Image

# A logo's SVG is a few kilobytes; anything far larger is not one.
MAX_SVG_BYTES = 512 * 1024
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")
_CLASSED = re.compile(r"<([A-Za-z][\w:-]*)(\s[^<>]*?)?\sclass=\"([^\"]+)\"([^<>]*?)(/?)>")


def is_svg_url(url):
    return str(url or "").split("?", 1)[0].split("#", 1)[0].lower().endswith(".svg")


def inline_classes(svg):
    """``svg`` with each class rule of its <style> blocks written into the classed elements' style."""
    rules = {}
    for style in re.findall(r"<style[^>]*>(.*?)</style>", svg, flags=re.S | re.I):
        style = re.sub(r"/\*.*?\*/|<!\[CDATA\[|\]\]>", "", style, flags=re.S)
        for selectors, body in _RULE.findall(style):
            body = body.strip().rstrip(";")
            for selector in selectors.split(","):
                selector = selector.strip()
                if re.fullmatch(r"\.[\w-]+", selector) and body:
                    rules[selector[1:]] = ";".join(part for part in (rules.get(selector[1:]), body) if part)
    if not rules:
        return svg

    def apply(match):
        tag, before, classes, after, closing = match.groups()
        declared = ";".join(rules[name] for name in classes.split() if name in rules)
        if not declared:
            return match.group(0)
        attributes = f"{before or ''} class=\"{classes}\"{after or ''}"
        existing = re.search(r"\sstyle=\"([^\"]*)\"", attributes)
        if existing:
            # The element's own style still wins: it comes after the class's rules.
            attributes = attributes.replace(existing.group(0), f" style=\"{declared};{existing.group(1)}\"", 1)
        else:
            attributes += f" style=\"{declared}\""
        return f"<{tag}{attributes}{closing}>"

    return _CLASSED.sub(apply, svg)


def render_svg(payload, side):
    """The SVG ``payload`` (bytes) drawn so its longer side is ``side`` px, as RGBA."""
    import fitz

    if len(payload) > MAX_SVG_BYTES:
        raise ValueError("SVG logo is too large")
    svg = inline_classes(payload.decode("utf-8", "replace"))
    with fitz.open(stream=svg.encode("utf-8"), filetype="svg") as document:
        page = document.load_page(0)
        longest = max(page.rect.width, page.rect.height)
        if longest <= 0:
            raise ValueError("SVG logo has no size")
        zoom = side / longest
        pixmap = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=True)
        image = Image.frombytes("RGBA", (pixmap.width, pixmap.height), pixmap.samples)
    if image.getchannel("A").getbbox() is None:
        raise ValueError("SVG logo drew nothing")
    return image


def svg_response_image(response, side=256):
    """The SVG a streamed HTTP ``response`` carries, drawn ``side`` px (its read bounded)."""
    response.raise_for_status()
    chunks, size = [], 0
    for chunk in response.iter_content(chunk_size=16384):
        size += len(chunk or b"")
        if size > MAX_SVG_BYTES:
            raise ValueError("SVG logo is too large")
        chunks.append(chunk)
    image = render_svg(b"".join(chunks), side)
    image.info["resource_downloaded_bytes"] = size
    return image
