### Summary
Discovery daily photos should use official Discovery photo gallery pages, not YouTube thumbnails.

### Details
The YouTube fallback source produced low-quality composite thumbnails with baked-in show titles, which did not match the requested clean daily-photo look. Discovery's public gallery pages can be read through `https://r.jina.ai/http://<discovery page>` and expose official `sndimg.com/content/dam/images/...` image URLs. Clean candidates should reject logos, breadcrumbs, 196x196 show-carousel cards, social art, cover art, `noTT`, and title-card style assets. Many 231x174 gallery URLs can be upgraded to `rend.hgtvcom.1280.720` and downloaded with a browser-like user agent.

### Suggested Action
For the `natgeo_photo_of_the_day` mixed source plugin, keep Discovery as a random source backed by official gallery pages such as `https://www.discovery.com/exploration/all-exploration-photos-pictures`, then overlay the user-provided Discovery logo only when the selected source is Discovery. Do not use YouTube thumbnails for this screen unless the user explicitly wants video-poster art.

### Metadata
- Source: user_feedback
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/natgeo_photo_of_the_day/natgeo_photo_of_the_day.py`, `inkypi-weather/package/InkyPi/src/plugins/natgeo_photo_of_the_day/discovery_logo.png`
- Tags: inkypi, discovery, natgeo, daily-photo, gallery, image-source, epaper
