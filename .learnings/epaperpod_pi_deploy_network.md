# EpaperPod Pi deploy network checks

## [LRN-20260528-108] workflow

**Logged**: 2026-05-28T19:35:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Use absolute Windows OpenSSH paths and bypass the local HTTP proxy when deploying to or validating the EpaperPod Pi from this workspace.

### Details
In this sandbox, plain `ssh`/`scp` can resolve to `C:\Users\super\.sbx-denybin\*.bat` and produce misleading `off / exit /b 1` output. Also, `curl.exe` sees `http_proxy=http://127.0.0.1:9`, so direct Pi HTTP checks can fail unless `--noproxy "*"` is supplied.

### Suggested Action
For Pi deploys, call `C:\Windows\System32\OpenSSH\scp.exe` and `C:\Windows\System32\OpenSSH\ssh.exe` explicitly with escalation. For Pi HTTP checks and image fetches, run `curl.exe --noproxy "*" ...`. If the Pi initially times out, retry once after a short delay before assuming the device is offline.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`
- Tags: inkypi, epaperpod, deploy, ssh, proxy

---
## [LRN-20260529-109] workflow

**Logged**: 2026-05-29T01:15:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
For EpaperPod plugin deploys, prefer direct `scp` to each target file over composing remote `cp && python -c ...` commands with nested quotes.

### Details
PowerShell plus SSH quote handling can strip quotes from remote inline Python or grep commands. A remote command can fail at parse time before earlier `cp` segments run, leaving the old plugin code in place while later checks appear unrelated. In this case the rendered image hash staying unchanged exposed that Simple Calendar had not actually picked up the font-size update.

### Suggested Action
Deploy edited plugin files by `scp` directly to both the running package path and `/usr/local/inkypi` mirror, then verify with a remote script or a simple single-purpose command. After rendering, compare the new current-image hash or visual screenshot against the previous one before claiming deployment success.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/simple_calendar/simple_calendar.py`
- Tags: inkypi, epaperpod, deploy, ssh, powershell, verification
