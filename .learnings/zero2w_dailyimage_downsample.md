# Zero 2 W DailyImage downsample rule

## [LRN-20260531-006] best_practice

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: infra

### Summary
Downsample DailyImage uploads before deploying them to `ColoredEpaperFrame`.

### Details
Uploading original iPhone images around 12MP to the `DailyImage` image_upload plugin caused the Raspberry Pi Zero 2 W service to be killed by systemd while a manual render and background cache render overlapped under `MemoryMax=200M`. After replacing the files with EXIF-transposed JPEGs capped at 1600px on the long edge, DailyImage rendered in about 2 seconds and completed the E6 display refresh without killing the service.

### Suggested Action
For `ColoredEpaperFrame` DailyImage updates:

```powershell
python .tmp\optimize_dailyimage.py
```

Then upload the optimized files to:

```text
/home/feeengyuuu/epaper_images/DailyImage/
```

Use the same filenames referenced in `device.json`. Avoid putting full-resolution phone originals directly on the Zero 2 W unless the service memory limit is intentionally revisited.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py`
- Tags: zero2w, dailyimage, image-upload, memory, epaperpod

---
