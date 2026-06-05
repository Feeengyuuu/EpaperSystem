# Ergou Daily requires detailed numeric news items

## [LRN-20260604-003] user_preference

**Logged**: 2026-06-04T18:02:00-07:00
**Priority**: high
**Status**: active
**Area**: automation

### Summary
`二狗新闻早报` should not use vague or summary-only news lines. Each news item needs concrete numbers and descriptive context.

### Details
The user rejected the preview because the news content was too generic: "我并不要笼统的新闻，我需要详细数字和描述". The workflow was updated so `headlines` and `incidents` prefer `{title, detail}` objects. Final img-2 prompts render a bold short title plus a smaller `细节：` line with specific dates, counts, percentages, locations, actions, and consequences.

### Suggested Action
For future runs, reject content that only says broad phrases like "多地发布" or "部门通报" without numbers and context. Preserve the two-level title/detail structure in the image prompt, even if it requires slightly fewer headline items or tighter wording.

### Metadata
- Source: Ergou Daily content-quality correction
- Related Files: tools/ergou_daily_mac/ergou_daily.py, tools/ergou_daily_mac/rules/ergou_daily_rules.md
- Tags: ergou_daily, news-detail, numeric-context, img-2
