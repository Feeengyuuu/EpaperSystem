# PowerShell quoting no-repeat rule

## [LRN-20260531-005] correction

**Logged**: 2026-05-31
**Priority**: high
**Status**: active
**Area**: infra

### Summary
Do not retry the same failed PowerShell/SSH/curl multi-layer quoting pattern during EpaperPod deployment.

### Details
During `ColoredEpaperFrame` migration, several commands failed because Windows PowerShell parsed remote shell syntax, JSON bodies, `$()`, `<`, `||`, or curl `@file` before SSH/curl could receive them. The user explicitly corrected this behavior: once this class of error happens, stop repeating it and change the execution strategy.

### Suggested Action
For complex remote commands, JSON POSTs, heredocs, or commands containing shell metacharacters, use one of these instead of inline quoting retries:

```powershell
# Prefer simple one-purpose commands only.
```

- Create a temporary script or JSON file with `apply_patch`.
- Upload it with `scp` if it must run remotely.
- Execute the script with a short command such as `bash ~/incoming/script.sh`.
- For local HTTP JSON calls from PowerShell, prefer `Invoke-WebRequest` or a short PowerShell script over `curl.exe` with hand-escaped JSON.

### Metadata
- Source: user_feedback
- Related Files: `docs/new-board-migration-baseline.md`
- Tags: powershell, ssh, curl, quoting, deployment, epaperpod

---
