# WoW Profile Dashboard Research

Date: 2026-06-04

## Scope

Add a small InkyPi plugin that renders a World of Warcraft profile dashboard for the US region by default.

## API Findings

- Official Battle.net OAuth docs: https://develop.battle.net/documentation/api-reference/oauth-api
- Official World of Warcraft Profile API docs: https://develop.battle.net/documentation/world-of-warcraft/profile-apis
- Public character profile calls can use an application token from client credentials.
- Account-wide "my WoW account" data requires a user OAuth access token with the `wow.profile` scope. A client id and client secret alone cannot list the user's private account characters.
- For US public character mode, the plugin should use `namespace=profile-us`, `locale=en_US`, `https://us.battle.net/oauth/token`, and `https://us.api.blizzard.com`.

## Key Check

- No local `.env` or `.secrets-backup` key name containing `blizzard`, `battle`, `bnet`, or `wow` was found during the initial non-secret scan.
- The live device `.env` had `WoW_Key` with length 32. No secret value was printed.
- `WoW_Key` by itself is not enough for the current Battle.net OAuth flow. Public character profile mode needs a client id plus client secret, or a ready bearer access token. Account mode needs a user OAuth token with `wow.profile`.
- The plugin accepts common aliases through `Config.load_env_key()` so user-managed API Key names can be mapped centrally.

## Design Direction

- Render a dense e-paper dashboard, not a marketing page.
- Use a restrained WoW-inspired palette: charcoal, parchment, gold, crimson, and arcane blue.
- Keep text inside fixed panels through measured shrink/wrap helpers.
- Prefer a useful setup/error screen over raising when key or character settings are missing, so playlist rotation is not blocked.
