# EpaperPod SFTP deploy and deferred restart

## [LRN-20260529-002] workflow

**Logged**: 2026-05-29T18:16:42-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Use SFTP batch as the fallback when direct SCP is reset, and treat timed-out `systemctl restart inkypi` as possibly still in progress.

### Details
During a BacktotheDate hot deploy to `feeengyuuu@192.168.1.186`, SSH login worked but both normal SCP and legacy `scp -O` failed immediately with `kex_exchange_identification: read: Connection reset`. A batch file with `sftp.exe -b` uploaded the same plugin file to both the active `/usr/local/inkypi/...` path and the running package mirror successfully.

The subsequent `sudo -n systemctl restart inkypi` timed out locally, but the restart continued remotely and stopped the service later, after a manual BacktotheDate display refresh had completed. The 7.3E display then took roughly 2.5 minutes at `Loading EPD display for epd7in3e display` before Waitress served port 80.

### Suggested Action
For future EpaperPod deploys, if SCP resets while SSH works, create a small SFTP batch file and run `C:\Windows\System32\OpenSSH\sftp.exe -b ...` with the project key. After any timed-out restart, do not immediately retry. Poll `systemctl is-active inkypi`, `journalctl -u inkypi -n ...`, and `/playlist` until Waitress returns HTTP 200.

### Metadata
- Source: deployment
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/backtothedate/backtothedate.py`
- Tags: inkypi, epaperpod, deployment, sftp, scp-reset, systemctl, epd7in3e
