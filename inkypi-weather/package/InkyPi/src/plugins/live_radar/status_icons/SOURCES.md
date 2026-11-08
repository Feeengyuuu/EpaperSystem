# LiveRadar status icon sources

`live.png` was generated with the built-in img-2 workflow for this LiveRadar
card-wall redesign. The generated glyph was placed on a flat chroma-key
background, converted to real alpha with the imagegen skill's
`remove_chroma_key.py` helper, cropped to its visible bounds, and
proportionally normalized onto a 64x64 transparent canvas.

`fav.png` (favourite) and `superfav.png` (super favourite, Mr.Quin's rooms) are
one pair generated on 2026-11-08 with GPT Image 2.6 Sunburst (Creative Claw job
ca12e890-8021-441a-a3fb-448af35ab1c1, 1536x768 transparent PNG): a plump
rounded five-point star with a dark ink outline, flat gold fill and highlights,
and the same star with a crimson heart at its centre inside a burst of eight
short gold rays. The owner chose this pair and asked for the plain star's
centre ring to be removed, so that ring was painted over with the star face's
own gold. The model's faint outer glow was dropped by alpha threshold, each
icon cropped to its visible bounds, centred on a square and scaled to 64x64.

No background color is baked into the final assets. The runtime retains the
glyphs' transparent background and pastes them directly without a badge border
or fill.
