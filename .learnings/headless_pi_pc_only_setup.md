# Headless Pi PC-only setup

## [LRN-20260531-003] project_context

**Logged**: 2026-05-31
**Priority**: medium
**Status**: active
**Area**: infra

### Summary
For the `ColoredEpaperFrame` migration, assume the user operates the Raspberry Pi headlessly from the PC, not from a keyboard/terminal attached to the Pi.

### Details
The user clarified that they can only operate from the PC and cannot directly type commands on the new board. Future setup guidance should give Windows PowerShell SSH steps first, using `ssh feeengyuuu@192.168.1.188`, then remote commands pasted into that SSH session.

### Suggested Action
When the new Pi needs manual bootstrap steps, phrase instructions as PC-side SSH workflows. Avoid saying "open a terminal on the new board" unless the user explicitly says they have direct console access.

### Metadata
- Source: user_feedback
- Related Files: `docs/new-board-migration-baseline.md`
- Tags: epaperpod, zero2w, ssh, onboarding, headless

---
