# Image Upload smoke temp permissions

## [LRN-20260530-001] environment

**Logged**: 2026-05-30
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
For Image Upload layout smoke checks, use a fixed workspace `.tmp` subdirectory instead of `tempfile` or `C:\tmp`.

### Details
During the `image_upload` portrait-column layout change, Python could write images under `G:\PersonalProjects\EpaperSystem\.tmp\image_upload_smoke`, but writes through `tempfile.mkdtemp()` and direct writes to `C:\tmp` failed with `PermissionError: [Errno 13] Permission denied`. Cleanup of generated files may also need escalated removal in this sandbox.

### Suggested Action
For future quick image smoke tests in this repo, create a deterministic directory under the workspace, for example:

```powershell
New-Item -ItemType Directory -Force -Path .tmp\image_upload_smoke | Out-Null
```

Then point ad-hoc runners at that directory and remove it afterward. Avoid long-running Python commands that use inaccessible temp paths.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/image_upload/image_upload.py`
- Tags: inkypi, image-upload, permissions, tempfile, smoke-test

---
