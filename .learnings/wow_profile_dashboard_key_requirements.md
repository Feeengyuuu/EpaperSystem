# WoW Profile Dashboard key requirements

## [LRN-20260604-003] key lookup workflow

**Logged**: 2026-06-04T22:25:00-07:00
**Priority**: high
**Status**: active
**Area**: epaperpod-runtime

### Summary
For the WoW profile dashboard, the live API Keys file used the name `WoW_Key`, but a single 32-character value is not enough for current Battle.net profile API access.

### Details
On `ColoredEpaperFrame`, the non-secret `.env` scan printed only key names and lengths and found `WoW_Key length=32`. No `BLIZZARD_CLIENT_SECRET`, `BNET_CLIENT_SECRET`, `BATTLE_NET_CLIENT_SECRET`, `WOW_CLIENT_SECRET`, `BLIZZARD_USER_ACCESS_TOKEN`, or `WOW_PROFILE_ACCESS_TOKEN` was present. Current Blizzard profile API access needs either client credentials (`BLIZZARD_CLIENT_ID` plus `BLIZZARD_CLIENT_SECRET`) for public character profile calls, a ready bearer access token, or a user OAuth token with `wow.profile` for account-wide private data.

### Suggested Action
- Keep `WoW_Key` as a central alias for `BLIZZARD_CLIENT_ID`, not as a complete token.
- Before live WoW API debugging, verify the device has a separate client secret or user OAuth token, printing only variable names and lengths.
- Use `C:\Windows\System32\OpenSSH\ssh.exe` with `.ssh/epaperpod_codex_20260525`; the default `ssh` command in this sandbox resolves to a deny wrapper.

### Metadata
- Source: live_debug
- Related Files: `inkypi-weather/package/InkyPi/src/config.py`, `inkypi-weather/package/InkyPi/src/plugins/wow_profile_dashboard/wow_profile_dashboard.py`
- Tags: api-keys, wow, blizzard, battle-net, oauth, ColoredEpaperFrame
