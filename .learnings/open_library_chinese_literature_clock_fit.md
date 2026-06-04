# Open Library fit for chinese_literature_clock

## [LRN-20260603-006] implementation and user preference

**Logged**: 2026-06-03
**Priority**: high
**Status**: active
**Area**: epaper, chinese-literature-clock, open-library

Workspace: G:\PersonalProjects\EpaperSystem

User ask: evaluate whether the Open Library API shown in the screenshot can be added to the Chinese literature clock plugin to obtain more Chinese book information.

Conclusion:
- Good fit as a best-effort metadata enrichment source for `chinese_literature_clock`.
- Not a replacement for the local quote dataset. Open Library returns bibliographic data, not curated Chinese time/quote lines.
- No API key is required for public lookup endpoints.

Current plugin shape:
- Plugin: `inkypi-weather/package/InkyPi/src/plugins/chinese_literature_clock/`
- Quote source: local CSV `data/chinese_litclock.csv`.
- Row fields: `time`, `time_human`, `full_quote`, `book_title`, `author_name`, `sfw`.
- Display currently renders quote, highlighted time text, and attribution only.

Useful Open Library API targets:
- `https://openlibrary.org/search.json`
  - Search by `title`, `author`, `q`, and limit returned fields.
  - Useful fields include `key`, `title`, `author_name`, `first_publish_year`, `language`, `cover_i`, `edition_count`, `subject`, `publisher`, `isbn`, `ia`.
  - Chinese filtering can use `language:chi` in the query or `language=chi` depending on the query strategy.
- `https://openlibrary.org/works/{olid}.json`
  - Fetch canonical work metadata when a stable work key is found.
- `https://openlibrary.org/books/{olid}.json` or `/isbn/{isbn}.json`
  - Fetch edition-level metadata when a stable edition/ISBN is known.
- `https://covers.openlibrary.org/b/id/{cover_i}-M.jpg`
  - Fetch book covers when `cover_i` is present.

Recommended integration:
1. Keep the CSV as source of truth for quote selection.
2. Add a small Open Library client for metadata lookup by `book_title` + `author_name`.
3. Cache metadata by normalized title/author for 30 days or longer.
4. Add an opt-in setting such as `show_book_metadata` or `open_library_enrichment`.
5. Render only a compact extra metadata line, e.g. first publish year, edition count, or cover-backed context, so the quote layout remains readable on e-paper.
6. Set a clear User-Agent header and cache responses, because Open Library explicitly asks clients to identify themselves and avoid bulk/high-frequency use.

Risks:
- Chinese coverage is useful but uneven; classics like Dream of the Red Chamber have Chinese records, but not every title/author pair will resolve cleanly.
- Search results can include commentaries, derivative works, or romanized records. Ranking should prefer exact title match, Chinese language records, matching author, and records with cover/edition count.
- Do not make per-refresh uncached calls for every display cycle. Use cache and fail closed to the current local-only display.

Decision:
- Safe to add as optional metadata enrichment.
- Implemented locally behind cache and a setting. Live deploy should only happen after the user asks for direct integration/deploy.

Implementation notes:
- `open_library.py` looks up metadata by selected `book_title` + `author_name`, ranks exact Chinese work matches, caches results for 30 days, and fails closed to the local-only source line.
- `quote_picker.py` now supports `source_random` and `source_daily`. These strategies keep the original time matching/fallback rules, then randomize by source group first so one book with many rows does not dominate the display.
- `settings.html` defaults new configurations to `source_random` and enables Open Library enrichment by default.
- `chinese_literature_clock.py` renders a bottom source block with a separator, source badge, book/author line, and compact Open Library metadata such as first publish year, edition count, publisher, and cache state.

User preference:
- For this plugin, the intended result is not merely a prettier attribution line. The user wants more sufficient source context and stronger randomness while preserving the original report-time matching rules.

Validation:
- Added `tests/test_chinese_literature_clock.py` for source-balanced random selection, Open Library ranking/cache behavior, source-block formatting, and renderer acceptance.
- Local pytest was unavailable in every project Python environment, so validation used bundled Codex Python plus project dependency path ordering. Smoke covered the same core behaviors and passed; AST parse also passed.
