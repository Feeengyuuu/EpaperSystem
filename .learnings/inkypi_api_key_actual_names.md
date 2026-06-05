# InkyPi API key actual names

## [LRN-20260604-002] key lookup workflow

**Logged**: 2026-06-04T17:22:00-07:00
**Priority**: high
**Status**: active
**Area**: epaperpod-runtime

### Summary
When a plugin needs an API key, inspect the live InkyPi `.env` / API Keys file for the user's actual key names before assuming canonical environment variable names.

### Details
On `ColoredEpaperFrame`, Robot initially missed Groq because code looked only for `GROQ_API_KEY`, while the API Keys UI stored non-empty Groq keys as `GROQ_KEY` and `Groq_V2`. The fix was to keep plugin calls using the logical `GROQ_API_KEY` name, then make `Config.load_env_key("GROQ_API_KEY")` accept the user-managed aliases `Groq_V2` and `GROQ_KEY`. After deployment, Robot state changed to `ai_message_provider: "groq"` and footer showed `AI Groq 1/24`.

### Suggested Action
- For any key-dependent plugin, first run non-secret checks against `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env`, printing only key names, presence, and length.
- Do not print secret values in shell output, logs, screenshots, or final responses.
- Prefer central aliases in `src/config.py` when a logical provider name has stable user-managed aliases.
- Do not generalize aliases across unrelated providers; only map the specific logical key being requested.

### Metadata
- Source: live_debug
- Related Files: `inkypi-weather/package/InkyPi/src/config.py`, `/home/feeengyuuu/inkypi-weather-pi-package-zero2w-20260531/InkyPi/.env`
- Tags: api-keys, groq, env-aliases, robot, ColoredEpaperFrame
