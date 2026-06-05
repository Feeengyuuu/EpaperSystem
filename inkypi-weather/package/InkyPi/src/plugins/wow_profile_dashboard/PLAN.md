# WoW Profile Dashboard Plan

Date: 2026-06-04

## Acceptance Criteria

- The plugin appears as `wow_profile_dashboard` with settings for region, realm slug, character name, locale, cache interval, mock mode, and force refresh.
- US region is the default and maps to `profile-us`.
- `Config.load_env_key()` recognizes common Blizzard/Battle.net/WoW aliases without leaking secret values.
- Public character mode obtains a Battle.net application token from client credentials or uses a supplied bearer token.
- Account-wide mode only runs when a user OAuth token is available; otherwise the screen clearly explains the limitation.
- Missing keys or missing character settings render a setup panel instead of crashing the refresh loop.
- Focused tests cover key aliases, OAuth/token routing, character data shaping, setup mode, and image rendering.

## Implementation Order

1. Add `Config` aliases for Blizzard logical keys.
2. Add the plugin directory, settings, research, and plan files.
3. Implement token loading, API routing, cache handling, payload normalization, and PIL rendering.
4. Add focused pytest coverage with fake network/session objects.
5. Run focused tests and AST validation.
6. Render a mock preview and inspect it for nonblank output and layout fit.
