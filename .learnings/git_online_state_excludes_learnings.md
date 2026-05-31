# Git online state excludes local learnings

## [LRN-20260527-054] user_preference

**Logged**: 2026-05-27T23:17:28-07:00
**Priority**: high
**Status**: active
**Area**: epaper

### Summary
When pushing EpaperSystem as an online snapshot for future hardware updates, exclude `.learnings` from the pushed commit.

### Details
The user wants GitHub to store the current machine/plugin code state so it can be restored or updated when the new mainboard arrives. `.learnings` is local agent memory and should remain on this machine unless the user explicitly asks to publish those notes.

### Suggested Action
Before pushing this repo, inspect the staged diff for `.learnings/`. If present, amend or restage so `.learnings` changes remain local and only device/plugin code, tests, assets, and useful tooling are included.

### Metadata
- Source: user correction
- Related Files: .learnings, inkypi-weather/package/InkyPi
- Tags: git, github, epaperpod, local-notes, backup
