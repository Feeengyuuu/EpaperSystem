# InkyPi service env resolution

## [LRN-20260531-007] environment quirk

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: epaperpod-runtime

### Summary
On `ColoredEpaperFrame`, the production service runs with `WorkingDirectory=/run/inkypi` and `/usr/local/inkypi/src` is a symlink to the package `InkyPi/src`; dotenv checks must include the realpath parent of `Config.BASE_DIR`.

### Details
`OPEN_AI_SECRET` existed in the package root `.env`, but `daily_ai_news` and `epaper_pet` could not see it in service-like execution until `Config.load_env_key()` loaded explicit candidates including `os.path.dirname(os.path.realpath(BASE_DIR))/.env`.

### Suggested Action
- When validating AI keys, test with `cwd=/run/inkypi` and `PYTHONPATH=/usr/local/inkypi/src`.
- Do not assume `load_dotenv()` with no path will find the package `.env` under systemd.
- This fix benefits all plugins that call `device_config.load_env_key()`, including `daily_ai_news` and `epaper_pet`.

### Metadata
- Source: live_debug
- Related Files: `inkypi-weather/package/InkyPi/src/config.py`, `inkypi-weather/package/InkyPi/src/plugins/daily_ai_news/daily_ai_news.py`, `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`
- Tags: dotenv, systemd, symlink, openai, robot, ColoredEpaperFrame

---
