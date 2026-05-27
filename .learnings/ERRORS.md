# Errors

Command failures and integration errors.

---

## [ERR-20260526-001] inkypi_update_now

**Logged**: 2026-05-26T01:58:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
`/update_now` can reset the connection if called before InkyPi's HTTP server is fully ready after a service restart.

### Error
```text
curl: (56) Recv failure: Connection reset by peer
```

### Context
- Operation attempted: Mini Weather manual refresh over SSH with `curl -X POST http://127.0.0.1/update_now`.
- Environment: Raspberry Pi InkyPi service had restarted and `systemctl is-active inkypi` was already `active`, but Waitress did not start serving until roughly three minutes after service start.
- A later `curl` readiness loop against `http://127.0.0.1/` returned `http-ready`, and the same `/update_now` request then succeeded.

### Suggested Fix
After restarting InkyPi on this Pi, wait for an HTTP readiness loop to pass before calling `/update_now` or fetching `/api/current_image`. Treat `systemctl is-active` as process state only, not web readiness.

### Metadata
- Reproducible: yes
- Related Files: inkypi-weather/package/InkyPi/src/plugins/mini_weather/mini_weather.py
- See Also: LRN-20260526-027

---
