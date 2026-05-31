### Summary
APOD should display a small transparent NASA logo overlay, using the user-provided clean PNG asset.

### Details
The first supplied NASA image had a checkerboard background baked into the pixels, so it needed background removal before it could be used as an overlay. The user then provided a better transparent asset at `G:/Download/b051bee3d8148b4733a017c6b43d5e5c.png`; this should be the visual source for the APOD logo. The production plugin stores it as `inkypi-weather/package/InkyPi/src/plugins/apod/nasa_logo.png`.

### Suggested Action
For future APOD layout work, keep the logo as a transparent local plugin asset and overlay it after the APOD image has been resized to the device dimensions. On the 800x480 horizontal e-paper screen, a bottom-left placement with roughly 64-96 px overall logo width and no background panel reads well without competing with the NASA image.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/apod/apod.py`, `inkypi-weather/package/InkyPi/src/plugins/apod/nasa_logo.png`
- Tags: inkypi, apod, nasa, logo, epaper, overlay
