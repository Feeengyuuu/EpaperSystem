# Ergou Daily needs generous safe margins

## [LRN-20260604-002] design_preference

**Logged**: 2026-06-04T17:50:00-07:00
**Priority**: high
**Status**: active
**Area**: automation

### Summary
For `二狗新闻早报` img-2 generations, keep wide side gutters and avoid edge-to-edge cards or text.

### Details
The first img-2 preview was visually too crowded on the left and right sides. The fix was to make the final image prompt explicitly require a 96px outer safe margin, a centered 832px reading column, at least 32px internal card padding, 24-32px vertical gaps, and a single-column main reading flow. The final image prompt should not include the full JSON schema rules, because that increases clutter and can confuse the image model.

### Suggested Action
When adjusting this workflow, preserve the safe-margin language in `brief_to_img2_prompt`. Keep the content compact before image generation instead of trying to fit long full-length news lines into edge-to-edge cards.

### Metadata
- Source: Ergou Daily Mac layout repair
- Related Files: tools/ergou_daily_mac/ergou_daily.py, tools/ergou_daily_mac/rules/ergou_daily_rules.md
- Tags: ergou_daily, img-2, layout, safe-margin, typography
