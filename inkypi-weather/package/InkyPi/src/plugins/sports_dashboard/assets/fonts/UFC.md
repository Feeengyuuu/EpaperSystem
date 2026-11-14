# Optional private UFC type

The UFC panel uses UFC Sans Condensed Medium for smaller information and
Condensed Bold for fighter names, headings and records when those original
WOFF2 files are installed. These are the UFC website's display faces verified
on 2026-11-14, not a claim about every broadcast package or future event.
The [type designer's UFC Sans project](https://crescenzi.co/p/ufc-sans)
documents the family; that attribution is not a download or purchase link.

`utils.ufc_font` reads `ufc-sans-condensed-medium.woff2` and
`ufc-sans-condensed-bold.woff2` from `INKYPI_DATA_DIR/fonts`. Supported Latin
letters and digits use UFC Sans; missing glyphs, including Chinese, use the
panel's existing font on the same baseline. Absent, empty, oversized, symlinked
or unreadable files retain the existing font. Font objects use the sports
render's weak cache instead of a permanent strong cache. Latin capitals and
digits are matched to the existing font's visible `8H` height, so the smaller
em proportions do not reduce the newly enlarged information.

The fixed installer purposes are `ufc_medium` and `ufc_bold`. Their original
metadata must be `UFC Sans` / `Condensed Medium` and `UFC Sans` /
`Condensed Bold`, respectively. Installation preserves the original WOFF2
bytes and uses the existing private-font toolkit after a successful release.
No renderer or font-DLC command downloads these brand fonts. The repository
and release archives exclude both installed basenames and original
`UFCSans-*.woff2` files; a public font URL does not establish redistribution
rights.
