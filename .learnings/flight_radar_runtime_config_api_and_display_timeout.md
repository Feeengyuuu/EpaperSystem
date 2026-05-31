# Flight radar runtime config updates

- When changing a live `SkyRadar` setting on `ColoredEpaperFrame`, prefer `/update_plugin_instance/SkyRadar` over direct `device.json` edits; the running service can rewrite JSON from its in-memory config.
- The runtime config path and package config path may resolve to the same file; guard copy operations with `samefile`.
- `/display_plugin_instance` can block long enough to time out and leave Waitress with a running request. If `/playlist` starts timing out afterward, restart `inkypi` and verify `/playlist` returns `200`.
