# Vehicle Status dashboard icon sources

The six PNG glyphs in this directory were generated on 2026-08-08 with the
built-in img-2 workflow for the Vehicle Status information dashboard. They are
original, generic automotive pictograms and do not reproduce a Tesla logo,
application icon, map marker, location UI, or command control.

The source prompt requested a neutral 3x2 set on a flat chroma-magenta
background: battery level, vehicle shield, fan and thermometer, vehicle and
gauge, tire pressure, and refresh clock. It explicitly excluded text, brands,
charging claims, locked-state claims, gradients, shadows, and fine detail.

The generated atlas had SHA-256
`f2b29d3adf1d3a701b08e6be59274ba624e9f18b9083f8d38388bff77b2be52a`.
The imagegen skill's `remove_chroma_key.py` helper produced an RGBA working
atlas with SHA-256
`7cd8ce3c14b29ca052641c87231b70f387ab1f44dc8c1c36584253614bf30867`.
Each cell was then cropped to its visible alpha bounds, proportionally fitted
inside 48x48 pixels, centered on a 64x64 transparent canvas, and reduced to a
binary alpha mask. The high-resolution working atlases are intentionally not
part of the InkyPi release.

At runtime the plugin uses only each glyph's alpha channel and recolors it from
the active day/night palette. Static icons remain neutral; live states such as
charging, unlocked, open closures, and tire warnings continue to be conveyed
by explicit text rather than by the icon artwork alone.
