# Dota Profile Uses Official Simplified Chinese Hero Names

## Learning
OpenDota `heroStats.localized_name` returns English hero names on the live device. For Simplified Chinese display, fetch Dota's official hero list from `https://www.dota2.com/datafeed/herolist?language=schinese` and apply `name_loc` by hero id before rendering.

## Context
- Plugin: `dota_profile_dashboard`
- Instance: `DailyDoseOfDay / DotaProfile`
- Live device: `ColoredEpaperFrame`
- Symptom: hero rows displayed `Anti-Mage`, `Axe`, `Bane`, and `Bloodseeker`.

## Recommended Pattern
- Cache official Simplified Chinese hero names as `hero_names_schinese.json`.
- Apply names by numeric `hero_id`, not by English display text.
- Normalize official `獸` to Simplified Chinese `兽`.
- Bump the plugin style/cache version so the existing rendered image is regenerated.

## Verification
After deployment and manual refresh, `DotaProfile` showed `敌法师`, `斧王`, `祸乱之源`, and `血魔`. The plugin instance image matched `/api/current_image` by SHA256, and the live cache contained `敌法师`, `祸乱之源`, and `兽`.
