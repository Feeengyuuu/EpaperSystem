# Theme Task 4 Report

Date: 2026-07-11

## Scope

- Updated exactly 56 built-in `src/plugins/<id>/plugin-info.json` manifests.
- Added `capabilities.supports_day_night_theme: true` to every built-in manifest.
- Added the exact Task 4 `theme.presentation`, day seed, and night seed mapping to every built-in manifest.
- Added `tests/test_plugin_theme_contract.py` as the exhaustive palette contract.
- Left `tests/test_plugin_resource_contract.py` unchanged because its existing schema-v2 gate already covers the shared manifest baseline.
- Did not modify renderers, `theme_utils`, `refresh_task`, queue, or runtime code.

## TDD Evidence

RED command:

```text
python -m pytest tests/test_plugin_theme_contract.py tests/test_plugin_resource_contract.py -q
```

Result before manifest edits: `1 failed, 5 passed`. The new contract failed at `ai_image` because `supports_day_night_theme` was still false, which was the expected missing-feature failure.

GREEN result after manifest edits: `6 passed in 1.76s`.

## Exact Mapping Evidence

A read-only audit parsed the Task 4 mapping directly from `docs/superpowers/plans/2026-07-11-per-plugin-day-night-theme.md` and compared every parsed value with every built-in manifest:

```text
PLAN_SEEDS=56 MANIFESTS=56 MATCHED=56
```

This covers plugin ids, presentation type, day background/accent, night background/accent, and the true capability flag.

## Focused Verification

```text
python -m pytest tests/test_plugin_theme_contract.py tests/test_plugin_resource_contract.py tests/test_plugin_manifest.py -q
95 passed in 2.86s

python -m ruff check tests/test_plugin_theme_contract.py tests/test_plugin_resource_contract.py tests/test_plugin_manifest.py
All checks passed!

git diff --check
exit 0
```

The stable Python 3.11 test environment specified for the task was used with `PYTHONPATH=src` and the InkyPi package as the working directory.

## Risks and Coordination

- This task declares palette ownership only; it intentionally does not change rendering or runtime selection behavior.
- The full suite was not run here to avoid contention with parallel agents. The parent agent will coordinate it after focused verification.
- Concurrent untracked refresh-policy files belong to another task and were not modified or staged here.
