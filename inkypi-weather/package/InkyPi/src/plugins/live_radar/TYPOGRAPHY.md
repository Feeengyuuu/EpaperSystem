# LiveRadar typography

At the user's request, text uses the original base UI font chain from before
`23662e14`: `get_base_ui_font(int(size), bold=weight == "bold")`. Where private
Microsoft YaHei fonts are installed, this selects their regular or true bold face.
Small bold usernames, room titles and uptime labels also use the bold face;
there is no 14 px threshold that silently changes them to a lighter weight.

Private YaHei files remain in the existing data directory and are never added
to the public repository. Installations without them retain the normal base UI
fallback: the bundled Noto Sans SC at 400/700, then the existing final fallback.
No fonts are downloaded or installed by this change.

The current card sizes, wrapping, 42 px main avatars, 18 px main usernames,
favorite-star reservation, compact uptime badges and remaining-width room
titles remain owned by the existing renderer and are not rolled back.

`readable_fonts.py` retains the existing weak render-font cache and the dashboard
and error-page render scopes. It shares base UI faces only while callers still
hold them, without introducing a strong font cache or changing shared glyph
loader caches. The former Inter/Noto split is no longer used by LiveRadar.
