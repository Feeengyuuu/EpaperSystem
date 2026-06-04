# ColoredEpaperFrame systemctl restart timeout

## [LRN-20260603-020] deployment

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, deployment

### Summary
`systemctl restart inkypi` can remain in `stop-sigterm` until the 4 minute stop timeout on ColoredEpaperFrame.

### Details
During the LoLInfo deploy, `sudo systemctl restart inkypi` stopped the old service but the Waitress/display thread did not exit promptly. The unit stayed `ActiveState=deactivating`, `SubState=stop-sigterm`, `ExecMainPID=2154` until systemd reached `TimeoutStopUSec=4min`, killed the old Python processes, and then started the new service. Non-interactive SSH did not allow follow-up `sudo kill` because sudo required a terminal/password, and the normal user could not kill the service process.

### Suggested Action
For future deploys, after a service restart starts stopping, poll `systemctl show inkypi -p ActiveState -p SubState -p ExecMainPID` and wait for the timeout path if needed. Do not assume a second non-interactive sudo command will work. Only proceed to web/API refresh after logs show `Started inkypi.service` and `waitress - Serving on http://0.0.0.0:80`, and `/playlist` returns HTTP 200.

### Metadata
- Source: production_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`
- Tags: colored-epaper-frame, epaperpod, deploy, systemd, waitress, restart-timeout
