# Windows OpenSSH full path

## [LRN-20260531-004] environment

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: infra

### Summary
When guiding the user to SSH into `ColoredEpaperFrame` from Windows PowerShell, use the full Windows OpenSSH path because plain `ssh` may not be in PATH.

### Details
The user's Administrator PowerShell reported `ssh` as an unrecognized command while trying to connect to `feeengyuuu@192.168.1.188`. The local automation environment successfully uses `C:\Windows\System32\OpenSSH\ssh.exe`.

### Suggested Action
For user-facing PC-side SSH instructions in this project, prefer:

```powershell
& "C:\Windows\System32\OpenSSH\ssh.exe" feeengyuuu@192.168.1.188
```

If that file is missing, fall back to enabling the Windows OpenSSH Client optional feature.

### Metadata
- Source: user_feedback
- Related Files: `docs/new-board-migration-baseline.md`
- Tags: windows, ssh, powershell, epaperpod, zero2w

---
