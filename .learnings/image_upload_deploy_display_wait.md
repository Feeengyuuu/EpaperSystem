# Image Upload deploy display wait

## [LRN-20260530-002] deployment

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
After a manual `DailyImage` display on `ColoredEpaperFrame`, wait for InkyPi to settle before judging deployment health.

### Details
Deploying the `image_upload` portrait-column layout to `ColoredEpaperFrame` succeeded and `POST /display_plugin_instance` returned `{"message":"Display updated","success":true}`. The plugin rendered and pushed the image to the display, but systemd then entered `deactivating`, timed out stopping the old process, and restarted InkyPi. The service recovered, `/playlist` returned 200, `/api/current_image` returned 200, and a later background `DailyImage` cache render completed with the new `Portrait layout` log.

### Suggested Action
For future `DailyImage` or image-heavy manual display deploys:

```text
1. Verify the changed plugin log line appears.
2. If systemctl reports deactivating, wait and poll instead of restarting again.
3. Finish only after systemctl is active, /playlist is 200, /api/current_image is 200, and a recent log shows the plugin completed or the service recovered cleanly.
```

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py`
- Tags: inkypi, image-upload, deployment, display-plugin-instance, systemd, colored-epaper-frame

---
