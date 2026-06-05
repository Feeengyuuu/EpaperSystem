# EpaperPod live deploy should avoid blocking SSH first steps

## [LRN-20260604-001] environment

**Logged**: 2026-06-04T19:10:00-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
Do not start EpaperPod deploy or verification with a potentially blocking SSH command against `ColoredEpaperFrame` (`192.168.1.188`). Probe with short HTTP requests first, and treat slow SSH as a signal to switch paths quickly.

### Details
SSH to `feeengyuuu@192.168.1.188` can hang long enough to interrupt the workflow even when command-level timeouts are present. This repeatedly caused long pauses during Steam Charts deploy verification. For live UI/plugin tasks, the user values fast progress updates and visible proof more than waiting on SSH.

### Suggested Action
For future `ColoredEpaperFrame` work, first check availability with `Invoke-WebRequest -TimeoutSec 5` against lightweight HTTP endpoints such as `/playlist` or `/api/current_image`. Use SSH only for unavoidable remote file/syntax/service operations, avoid parallel SSH probes, and abandon or switch to `sftp.exe -b` / HTTP API if a connection does not return quickly. Tell the user immediately when deployment is still local because remote verification is blocked.

### Metadata
- Source: user correction
- Related Files: inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.css
- Tags: inkypi, epaperpod, coloredepaperframe, ssh, deploy, verification, steam-charts
