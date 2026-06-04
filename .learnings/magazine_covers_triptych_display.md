# Magazine Covers Triptych Display

For the `magazine_covers` plugin, the preferred e-paper display is now a three-cover triptych: three plain cover images fitted into equal columns over a soft blurred backdrop sampled from the first cover.

Do not add frames, card backgrounds, mattes, or drop shadows around the covers. Single-cover non-crop modes should use plain contain on a solid background; only `cover` mode should crop.

When deploying this plugin on the Pi, update the `MagazineCovers` instance settings to keep `fitMode=triptych`, `backgroundStyle=blur`, and `dailyLibraryMode=true`.
