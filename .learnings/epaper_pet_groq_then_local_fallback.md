# Epaper pet Groq to local fallback

## [LRN-20260604-001] runtime behavior

**Logged**: 2026-06-04T17:12:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaperpod-runtime

### Summary
For `Robot` / `epaper_pet`, `free_auto` and `groq` should resolve AI dialogue backends as Groq first, then the local rules generator. Local fallback should still generate a line when Groq is missing, rate-limited, or past the configured remote daily limit.

### Details
The live `ColoredEpaperFrame` package had no non-empty canonical `GROQ_API_KEY` in `.env`, but the user's API Keys UI stored Groq keys under `GROQ_KEY` and `Groq_V2`. Before alias support was added, Robot refreshes fell back to local generation with `ai_message_provider: "local"`, `ai_message_model: "local-rules-v1"`, `ai_message_fallback_from: "groq"`, and `ai_message_fallback_reason: "missing_groq_key"`. After `Config.load_env_key("GROQ_API_KEY")` accepted those aliases, Robot generated with `ai_message_provider: "groq"`. The local fallback footer intentionally renders as `AI Local 0/24 <- Groq` when Groq is not available.

### Suggested Action
- Verify Robot AI origin from `/usr/local/inkypi/src/plugins/epaper_pet/cache/pets/loki.json`, not only the footer.
- Use the test path `.venv-test` plus `.pc-packages` and the Windows `os.mkdir` permission workaround for `tests/test_epaper_pet_context.py`.
- After deployment, trigger `POST /display_plugin_instance` with `DailyDoseOfDay`, `epaper_pet`, and `Robot`, then confirm `journalctl -u inkypi` has an `Updating display` line for Robot.

### Metadata
- Source: live_deploy
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `inkypi-weather/package/InkyPi/tests/test_epaper_pet_context.py`, `/usr/local/inkypi/src/plugins/epaper_pet/cache/pets/loki.json`
- Tags: epaper-pet, robot, groq, local-fallback, ai-telemetry, ColoredEpaperFrame
