### Summary
National Geographic Photo of the Day is usable as an InkyPi daily image source when fetched from the official page metadata.

### Details
The official `https://www.nationalgeographic.com/photo-of-the-day` page exposes the current image through metadata, and the image URL downloaded consistently in repeated Windows and Raspberry Pi tests. The source does not require a browser screenshot for the current observed page. On the Pi, requesting `image/avif` caused the CDN to return an image format unsupported by the deployed Pillow build, so requests must prefer `image/jpeg,image/png` instead of broad modern browser image Accept headers.

### Suggested Action
For a production plugin, fetch the official Photo of the Day HTML, select the NatGeo image from metadata first, force JPEG/PNG image Accept headers, render to 800x480 locally, and add a fixed National Geographic logo badge in the final composition. The official NatGeo structured-data logo asset is `https://assets-cdn.nationalgeographic.com/natgeo/static/icons/redesign-logo.svg`; rasterize/package it as a PNG asset because the Pi runtime does not have a general SVG renderer. Do not add a white or translucent backing panel behind the logo; the preferred look is the transparent official logo directly over the photo. Do not fall back to a hand-drawn logo if the official raster asset is missing; omit the logo instead so the output does not appear to revert. The user-provided transparent PNG logo from `G:/Download/7e29fc6ffbba3f7c18e528d721df4d22.png` is the preferred visual asset for this screen; package it with the production plugin rather than re-generating the logo at runtime.

### Metadata
- Source: implementation
- Related Files: `tools/probe_natgeo_potd.py`
- Tags: inkypi, natgeo, photo-of-the-day, epaper, image-fetching, raspberry-pi
