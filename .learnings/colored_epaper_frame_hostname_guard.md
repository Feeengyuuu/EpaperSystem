# ColoredEpaperFrame hostname guard

## [LRN-20260604-007] best_practice

**Logged**: 2026-06-04
**Priority**: high
**Status**: active
**Area**: epaper, deployment, device-targeting

### Summary
Do not deploy to the first reachable InkyPi device; verify the target hostname is `ColoredEpaperFrame`.

### Details
During LoLInfo random-playlist deployment, `192.168.1.188` briefly disappeared and `192.168.1.187` was reachable. That reachable device was `EpaperPodBeta`, not the intended target. The correct live target came back at `192.168.1.188`, and SSH `hostname` returned `ColoredEpaperFrame`.

### Suggested Action
Before any live deployment for the main frame, verify both HTTP reachability and SSH identity with `hostname`. If a scan finds `.187` or another reachable InkyPi-like device, treat it as non-target unless it identifies as `ColoredEpaperFrame`. If a wrong target is modified during recovery, remove any added playlist instance before finishing.

### Metadata
- Source: deployment_correction
- Related Files: `tools/add_lol_info_to_random_list.py`, `inkypi-weather/package/InkyPi/src/plugins/lol_info/lol_info.py`, `.learnings/colored_epaper_frame_identity.md`
- Tags: epaperpod, colored-epaper-frame, hostname, deployment, device-identity, random-playlist

---
