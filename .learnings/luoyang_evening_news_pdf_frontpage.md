# Luoyang Evening News PDF front page

## [LRN-20260603-001] discovery

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaperpod-newspaper

### Summary
Luoyang Evening News should be treated as an official A01 PDF/front-page source, not as a browser screenshot source.

### Details
The public digital newspaper platform exposes historical A01 PDFs under a predictable path such as `https://lywb.lyd.com.cn/images2/2/YYYY-MM/DD/A01/YYYYMMDDA01_pdf.pdf`. Current availability should be checked by publication date, because public reporting says the paper moved from six issues per week to Wednesday/Friday issues in 2026. Local bare `curl` requests can receive `403 Forbidden` from the CDN even when browser/search access can read older PDFs, so production verification should test the actual Pi/network path before deployment.

### Suggested Action
- Implement a dedicated `lywb` or `pdf` newspaper source that tries recent publication dates, downloads the A01 PDF, rasterizes page 1, and fits/crops it for the e-paper display.
- Poll daily, but update only when a new issue exists; on non-publication days keep the prior issue or show a clear no-new-issue state.
- Avoid Chromium website screenshots for this source. Prior newspaper work found Chromium unreliable on the Pi for newspaper-style screenshots.

### Metadata
- Source: research
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py`
- Tags: epaperpod, newspaper, luoyang-evening-news, lywb, pdf, front-page

---
