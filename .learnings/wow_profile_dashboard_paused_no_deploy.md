# WoW profile dashboard paused without deployment

## [LRN-20260605-001] rollout gate

**Logged**: 2026-06-05T00:00:00-07:00
**Priority**: medium
**Status**: active
**Area**: epaperpod-runtime

### Summary
The WoW profile dashboard prototype should remain local and should not be uploaded or deployed unless the user explicitly resumes it.

### Details
After the local prototype reached a mock-renderable paper-doll equipment layout, the user decided the plugin was not worth continuing for now and said to set it aside without uploading. The live device also only had `WoW_Key` and lacked the separate Battle.net client secret or user OAuth token needed for real profile API access.

### Suggested Action
- Do not deploy `wow_profile_dashboard` during broad plugin upload passes unless the user explicitly asks to resume WoW work.
- If resumed, first ask for or verify `BLIZZARD_CLIENT_SECRET` / `WOW_CLIENT_SECRET` or a user OAuth token with `wow.profile`.
- Treat existing local files as a paused prototype, not production-ready live runtime code.

### Metadata
- Source: user_decision
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/wow_profile_dashboard/`, `inkypi-weather/package/InkyPi/src/config.py`
- Tags: wow, blizzard, deploy-gate, prototype, ColoredEpaperFrame
