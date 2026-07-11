# Task 3 Report: Configurable YaHei Defaults

Date: 2026-07-10

## Outcome

- Missing or empty configurable font values now use `DEFAULT_FONT_FAMILY` (`Microsoft YaHei`).
- Internal `__cjk__` requests in Daily Knowledge and Daily Wiki use `get_base_ui_font`.
- Non-empty explicit choices remain unchanged, including `Jost`, `LXGW WenKai`, `方正新楷近似`, and `康熙字典体`.
- New-instance settings default to Microsoft YaHei without rewriting a stored explicit choice.

## Scope

Changed only the five Task 3 plugin modules, their five settings templates, and their five corresponding test modules. The brief listed four settings templates, but `daily_wiki_page/settings.html` still contained a legacy-Jost rewrite that contradicted the explicit preserve interface. It was included as the minimum interface-driven correction after a dedicated failing regression test.

No device configuration migration, Task 4 work, live deployment, or font-binary changes were performed.

## RED Evidence

### Environment preflight

Command:

```powershell
python -m pytest tests/test_daily_art.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_tech_pulse.py -q -k "font or settings"
```

Result: collection was blocked under the global Python because `flask` and `pytz` were absent. This was not counted as behavioral RED. The worktree does not contain ignored virtual environments, so subsequent commands used the existing repository `.venv` interpreter while keeping the working directory and imported source in this worktree.

### Configurable default and resolver RED

Command:

```powershell
$testPython = "G:\PersonalProjects\EpaperSystem\inkypi-weather\package\InkyPi\.venv\Scripts\python.exe"
& $testPython -m pytest tests/test_daily_art.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_tech_pulse.py -q -k "font or settings"
```

Result: `11 failed, 7 passed, 98 deselected`.

Expected failures proved that:

- Daily Art, Daily Knowledge, Daily Word/Poem, and Tech Pulse still defaulted to Jost.
- Daily Wiki rewrote an explicit Jost selection to YaHei.
- Daily Knowledge rewrote an explicit Chinese literary family.
- Daily Knowledge and Daily Wiki did not route `__cjk__` through the shared resolver.
- Four settings templates still selected Jost for empty/new values.

### Daily Wiki settings preserve RED

Command:

```powershell
& $testPython -m pytest tests/test_daily_wiki_page.py -q -k "daily_wiki_font_defaults"
```

Result before the template fix: `1 failed, 54 deselected`. The assertion found the legacy `fontFamily.value === 'Jost'` rewrite.

## GREEN Evidence

Focused configurable-font command after the Python/default/settings implementation:

```text
18 passed, 98 deselected
```

Daily Wiki settings preserve test after removing only the legacy-Jost condition:

```text
1 passed, 54 deselected
```

Fresh full five-suite command:

```powershell
& $testPython -m pytest tests/test_daily_art.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_tech_pulse.py -q
```

Result: `116 passed in 5.98s`.

Ruff command covered all five changed plugin modules and all five changed test modules:

```powershell
& $testPython -m ruff check src/plugins/daily_art/daily_art.py src/plugins/daily_knowledge/daily_knowledge.py src/plugins/daily_wiki_page/daily_wiki_page.py src/plugins/daily_word_poem/daily_word_poem.py src/plugins/tech_pulse/tech_pulse.py tests/test_daily_art.py tests/test_daily_knowledge.py tests/test_daily_wiki_page.py tests/test_daily_word_poem.py tests/test_tech_pulse.py
```

Result: `All checks passed!`

Final whitespace and scope gate:

```powershell
git diff --check
git diff --name-only
```

Result: `git diff --check` exited 0, and `git diff --name-only` contained only the five plugin modules, five settings templates, and five corresponding tests described above. This report is ignored by the repository's normal untracked-file rules and is force-added explicitly for the Task 3 handoff.

## Risks and Boundaries

- Tests used the main checkout's ignored `.venv` only as an interpreter/dependency source; pytest ran from this worktree and loaded this worktree's `src` tree.
- Settings preservation is source/static regression tested. No browser automation or live-device acceptance was run because those belong to Task 4.
- The shared resolver can still fall back when YaHei files are unavailable; that fallback policy is owned by Task 1 and was not changed here.
