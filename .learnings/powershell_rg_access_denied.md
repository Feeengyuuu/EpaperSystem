# PowerShell rg access denied fallback

## [LRN-20260531-001] environment

**Logged**: 2026-05-31
**Priority**: low
**Status**: active
**Area**: docs

### Summary
In this workspace, `rg.exe` may fail with `Access is denied`; use PowerShell-native file discovery and search as the fallback.

### Details
During the Zero 2 W migration runbook update, `rg --files` failed before returning project paths with `Program 'rg.exe' failed to run: Access is denied`. PowerShell commands such as `Get-ChildItem`, `Get-Content`, and `Select-String` worked normally.

### Suggested Action
When `rg` is blocked in `G:\PersonalProjects\EpaperSystem`, continue with:

```powershell
Get-ChildItem -Force
Get-ChildItem -Recurse -File
Select-String -Path <path> -Pattern '<pattern>'
```

Keep using `rg` first in other workspaces, but do not spend time debugging this workspace-specific denial unless search itself becomes blocked.

### Metadata
- Source: error
- Related Files: `docs/new-board-migration-baseline.md`
- Tags: powershell, rg, permissions, docs

---
