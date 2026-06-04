# Robot AI provider status

## [LRN-20260602-001] runtime diagnostic

**Logged**: 2026-06-02T23:57:40-07:00
**Priority**: medium
**Status**: active
**Area**: epaperpod-runtime

### Summary
When the `Robot` (`epaper_pet`) instance shows local AI telemetry or does not show Groq usage, inspect the live pet state before assuming the rendered line is AI-generated.

### Details
On `ColoredEpaperFrame` (`192.168.1.188`), the live state file `/usr/local/inkypi/src/plugins/epaper_pet/cache/pets/loki.json` showed `message: "Cached a small dream for the next refresh."`, `last_event_key: "dream_cache"`, and `ai_message_status: "missing_free_provider"` after a `Robot` cache refresh at 2026-06-02 23:42:27. That means the visible line came from the local pet event table, not from Groq/OpenAI. The active package `.env` existed at `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env`, but a non-printing `grep -q '^GROQ_API_KEY=.' ...` check found no non-empty Groq key line.

### Suggested Action
- Check `plugins/epaper_pet/cache/pets/loki.json` for `ai_message_provider`, `ai_message_status`, `ai_usage`, `last_event_key`, and `message`.
- In `free_auto`, no `GROQ_API_KEY` means `_resolve_ai_backends()` returns no free backend and the footer falls back to local telemetry such as `AI Local 0/24`.
- Remember that `Robot` has `ai_dialogue=on` and `ai_each_render=on`, but state-changing pet events can write a local line before the AI path runs.

### Metadata
- Source: live_debug
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`, `/usr/local/inkypi/src/plugins/epaper_pet/cache/pets/loki.json`
- Tags: epaper-pet, robot, groq, ai-telemetry, ColoredEpaperFrame
