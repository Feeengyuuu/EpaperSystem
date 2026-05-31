# ColoredEpaperFrame board identity

## [LRN-20260531-002] project_context

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: infra

### Summary
The new Raspberry Pi Zero 2 W board for the EpaperSystem migration is named `ColoredEpaperFrame`.

### Details
The user explicitly provided the new board name after the migration baseline was created. Treat `ColoredEpaperFrame` as the target hostname and InkyPi device name for the future one-to-one replication from the current beta board.

### Suggested Action
When deployment starts, set or verify the new board hostname as `ColoredEpaperFrame`, prefer `ColoredEpaperFrame.local` when mDNS resolves, and do not reuse `EpaperPodBeta` or `EpaperBeta` for the new board identity.

### Metadata
- Source: user_feedback
- Related Files: `docs/new-board-migration-baseline.md`
- Tags: epaperpod, zero2w, hostname, migration

---
