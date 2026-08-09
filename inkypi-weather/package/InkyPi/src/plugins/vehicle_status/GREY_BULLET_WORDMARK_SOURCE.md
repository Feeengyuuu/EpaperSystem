# Grey Bullet wordmark source

`grey_bullet_wordmark.png` is an original wordmark generated on 2026-08-08
with the built-in img-2 workflow for the Vehicle Status header.

The selected source requested the exact single-line text `GREY BULLET` in a
minimal, bold, condensed automotive instrument style. The prompt explicitly
excluded Tesla branding, car silhouettes, firearms, projectiles, badges,
separate icons, extra text, gradients, shadows, and watermarks. A flat green
background was requested only for deterministic removal.

The selected chroma-key source had SHA-256
`b43ea0d5f7fdd8ad54a692c1b12548a2f23f6d546738a72ec5b1f22b7eb9c0e9`.
The imagegen skill's `remove_chroma_key.py` helper produced an RGBA working
image with SHA-256
`8bdac3e25a287ab3698585dc2de0554153c4c416cf3e726ac52c973fd9cbdc8d`.

For the constrained header, the visible alpha bounds were cropped, resized to
a 720x156 mask, and centered with 8px transparent padding on a 736x172 canvas.
The final 15,290-byte PNG has SHA-256
`f013b813f3bcc6ff0a74f8d892591cb844c6885b2a11642aea6a8213aaf01b2e`.
The high-resolution working files are intentionally excluded from releases.

At runtime only the alpha mask is used. The renderer recolors the wordmark with
the active day or night ink color and uses it only for normalized `Gray Bullet`
or `Grey Bullet` names. Other names, missing assets, and decoding failures keep
the normal dynamic text path.
