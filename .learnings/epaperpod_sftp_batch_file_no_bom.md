# EpaperPod SFTP batch files should be real ASCII files

## [LRN-20260604-002] environment

**Logged**: 2026-06-04T20:05:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
When using Windows OpenSSH `sftp.exe -b` for EpaperPod deployment, use a real ASCII `.sftp` batch file instead of piping a PowerShell here-string.

### Details
PowerShell here-strings piped into `sftp.exe -b -` can prepend BOM/mojibake characters. In this Steam Charts deployment, the remote connection succeeded with the project key, but `sftp` rejected the first command as `Invalid command` because the batch started with BOM-like characters before `put`.

### Suggested Action
Create the deployment batch under `.tmp/*.sftp` with `apply_patch`, then run `sftp.exe -b .tmp/<file>.sftp` using `.ssh/epaperpod_codex_20260525`, `BatchMode=yes`, `IdentitiesOnly=yes`, and the project known-hosts file. Avoid piping PowerShell here-strings into `sftp` for production deploy steps.

### Metadata
- Source: implementation
- Related Files: .tmp/deploy_steam_charts_header_fix.sftp, inkypi-weather/package/InkyPi/src/plugins/steam_charts/steam_charts.py, inkypi-weather/package/InkyPi/src/plugins/steam_charts/render/steam_charts.css
- Tags: inkypi, epaperpod, coloredepaperframe, sftp, powershell, bom, deployment
