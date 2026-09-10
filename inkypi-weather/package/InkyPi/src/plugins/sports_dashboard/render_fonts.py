"""Share fonts already owned by a render without extending their lifetime."""

from contextlib import contextmanager
from contextvars import ContextVar
from weakref import WeakValueDictionary


_FONTS = ContextVar("sports_render_fonts", default=None)
_MAX_ACTIVE_KEYS = 128


@contextmanager
def render_font_scope():
    # Each render, including nested renders, owns a separate lookup. Values are
    # weak: the caller's last reference releases the native font immediately.
    fonts = WeakValueDictionary()
    token = _FONTS.set(fonts)
    try:
        yield
    finally:
        fonts.clear()
        _FONTS.reset(token)


def render_font(loader, size, bold=False):
    size, bold = int(size), bool(bold)
    fonts = _FONTS.get()
    if fonts is None:
        return loader(size, bold=bold)
    key = (loader, size, bold)
    font = fonts.get(key)
    if font is not None:
        return font
    font = loader(size, bold=bold)
    if len(fonts) < _MAX_ACTIVE_KEYS:
        try:
            fonts[key] = font
        except TypeError:
            # Compatibility with alternate font implementations without weakrefs.
            pass
    return font
