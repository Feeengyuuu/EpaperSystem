# ColoredEpaperFrame systemd runtime path

## [LRN-20260601-004] deployment

**Logged**: 2026-06-01T02:20:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
For ColoredEpaperFrame deploys, confirm the active systemd ExecStart/CGroup path; `/usr/local/inkypi/src` may be a symlink, and file edits must land in the path the service is actually running.

### Details
During the `box_office_top_movies` deploy, `/usr/local/inkypi/src` resolved through a symlink to `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src`, while `systemctl status inkypi` showed the running process as `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/src/inkypi.py`. A cleanup attempt against the plugin directory removed source files but failed to remove a root-owned `__pycache__`, leaving a partial plugin directory until the three runtime files were re-uploaded directly to the active package path.

### Suggested Action
Before deployment, run `systemctl status inkypi --no-pager -l` and use the displayed runtime package path as the source of truth. For plugin deploys, upload or copy only `*.py`, `plugin-info.json`, `settings.html`, and required assets; avoid recursive deletion of plugin directories that may contain root-owned `__pycache__`. Use `PYTHONPYCACHEPREFIX=/tmp/inkypi-pycache ... -m py_compile` for remote validation.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/box_office_top_movies/box_office_top_movies.py`
- Tags: colored-epaper-frame, inkypi, deployment, systemd, symlink, pycache
