# Errors

Command failures and integration errors.

---

## [ERR-20260529-003] windows_pytest_tmpdir_permissions

**Logged**: 2026-05-29T19:32:37-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
In this Windows Codex desktop environment, pytest temp directory handling can fail before tests run because generated basetemp directories become inaccessible to Python.

### Error
```text
PermissionError: [WinError 5] Access is denied:
'C:\\Users\\super\\AppData\\Local\\Temp\\pytest-of-LocalTest'

PermissionError: [WinError 5] Access is denied:
'G:\\PersonalProjects\\EpaperSystem\\...\\pytest-bambu-codex-run2'
```

### Context
- Operation attempted: run `tests/test_bambu_monitor.py` with `.venv-test\\Scripts\\python.exe -m pytest`.
- Required dependency path: set `PYTHONPATH=.pc-packages;src` so Python 3.12 can see the project dependency bundle.
- `pytest` collected tests, but its `tmp_path` fixture and basetemp cleanup hit permission errors. Direct Python assertions using a normal manually-created directory succeeded.

### Suggested Fix
For quick plugin render checks in this environment, prefer a direct assertion script using `.venv-test` plus `PYTHONPATH=.pc-packages;src` when pytest fails at temp setup. If pytest is required, investigate why pytest's Windows `mode=0o700` temp directories become inaccessible under the sandbox before relying on `tmp_path`.

### Metadata
- Reproducible: yes
- Related Files: inkypi-weather/package/InkyPi/tests/test_bambu_monitor.py
- See Also: none

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

## [ERR-20260529-002] dns-sd_browse

**Logged**: 2026-05-29T15:38:59-07:00
**Priority**: medium
**Status**: active
**Area**: infra

### Summary
`dns-sd -B` is a continuous browse command and can hang a Codex desktop turn if invoked directly.

### Error
```text
dns-sd -B _ssh._tcp local
dns-sd -B _http._tcp local
```

### Context
- Operation attempted: browse LAN mDNS services while trying to locate the Raspberry Pi e-paper device.
- Environment: Windows PowerShell in Codex desktop.
- The commands kept running until the user interrupted the turn; two `dns-sd` processes remained and had to be stopped manually.

### Suggested Fix
Avoid direct `dns-sd -B` in this environment. Prefer bounded DNS lookups, ARP/neighbor table checks, or run `dns-sd` only under an explicit external process wrapper that kills it after a short capture window.

### Metadata
- Reproducible: yes
- Related Files: none
- See Also: LRN-20260529-111

---

## [ERR-20260530-004] powershell_python_stdin_mojibake_prefix

**Logged**: 2026-05-30T22:42:19-07:00
**Priority**: medium
**Status**: active
**Area**: tests

### Summary
PowerShell here-strings piped into `python -` can arrive with a mojibake/BOM-like prefix before the first source token in this Codex desktop environment.

### Error
```text
SyntaxError: invalid non-printable character U+FEFF
SyntaxError: invalid syntax. Did you mean 'import'?
stdin prefix observed as code points: 0x9518, 0x5321, 0x8c62 before "import"
```

### Context
- Operation attempted: run an inline Python render-preview script through a PowerShell here-string pipe.
- Environment: Windows PowerShell inside Codex desktop, `cwd=G:\PersonalProjects\EpaperSystem`.
- Stripping only `\ufeff` was not sufficient because the visible prefix decoded as mojibake characters before the real Python source.

### Suggested Fix
For ad hoc inline Python piped from PowerShell, either use a single-line `python -c` when practical, or make the Python launcher discard everything before a known first token such as `import json` before compiling stdin.

### Metadata
- Reproducible: yes
- Related Files: none
- See Also: none

---
