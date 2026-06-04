# Marvel App token capability probe

## [LRN-20260603-004] capability-probe

**Logged**: 2026-06-03
**Priority**: medium
**Status**: active
**Area**: epaper, api-keys, comic-covers

### Summary
`Marvel_KEY` is a Marvel App design-platform personal access token, not a Marvel Comics developer API key. It should not be used for official Marvel comic metadata or comic-cover lookup.

### Probe Result
A live, value-redacted probe against `https://api.marvelapp.com/graphql/` succeeded with HTTP 200. The key was read from the live `.env` as `Marvel_KEY`; only length and SHA-256 prefix were used to confirm identity. The key value was not printed or written.

The token can read the authenticated Marvel App account identity. The current account returned zero accessible projects in the first project-page probe, so no project screens or design assets were available to sample at probe time.

### Capability Shape
- Confirmed endpoint: Marvel App GraphQL, `https://api.marvelapp.com/graphql/`.
- Confirmed schema access: 16 root query fields and 51 mutation fields were visible through introspection.
- Relevant query surfaces include `user`, `project`, `folder`, `team`, `userTestDocument`, `userTestMediaStats`, and `userTestStepStats`.
- Mutations are visible in the schema, but the current token should be treated as read-only unless write scopes are explicitly enabled.

### Plugin Implications
- Not useful for `gcd_comic_covers` if the goal is official comic cover discovery from Marvel Comics.
- Potentially useful for a separate Marvel App prototype/design showcase plugin, or for rendering user-uploaded project screens/design assets if accessible projects exist.
- If official Marvel Comics covers are needed, use the Marvel Comics API credentials instead. That flow normally requires Marvel Comics public/private credentials and signed requests, not this Marvel App personal access token.

### References
- Marvel App docs: `https://marvelapp.com/developers/documentation`
- Marvel App getting started: `https://marvelapp.com/developers/documentation/getting-started`
- Marvel Comics developer docs: `https://developer.marvel.com/documentation/getting_started`
