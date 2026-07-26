# LiveRadar status icon sources

`live.png` and `fav.png` were generated with the built-in img-2 workflow for
this LiveRadar card-wall redesign. The generated glyphs were placed on flat
chroma-key backgrounds, converted to real alpha with the imagegen skill's
`remove_chroma_key.py` helper, cropped to their visible bounds, and
proportionally normalized onto a 64x64 transparent canvas.

No background color is baked into the final assets. The runtime retains the
glyphs' transparent background and pastes them directly without a badge border
or fill.
