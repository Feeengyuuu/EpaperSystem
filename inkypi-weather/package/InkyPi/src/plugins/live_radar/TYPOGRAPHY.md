# LiveRadar typography

Card text pairs the bundled Inter SemiBold with Noto Sans SC at weight 500.
Labels requested as bold at 14 px or larger pair Inter Bold with Noto Sans SC
at weight 600. The selected sizes, wrapping and card geometry stay with the
existing renderer. Missing Latin glyphs use Noto Sans SC on the same baseline.
This matches the accepted font preview of 2026-11-14.

Both families are already shipped under the SIL Open Font License: Inter's
source and licence are in `../flight_radar/assets/fonts`, and Noto Sans SC's
licence is in `../../static/fonts/NotoSansSC-OFL.txt`. No private font download
or installation is required.

`readable_fonts.py` uses the existing sports renderer's weak font cache. The
dashboard and error-page render methods own its scope. Per-size font pairs are
weakly shared; this introduces no persistent strong cache. The shared glyph
loader can retain its existing per-file probe and rasterization caches, so the
scope does not promise the lifetime of every underlying native font. If a
bundled face is unavailable or unreadable, the
loader keeps the available CJK face or the normal UI fallback.
