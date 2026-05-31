# InkyPi Python test runtime

## [LRN-20260529-001] environment

**Logged**: 2026-05-29T17:54:22-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Run focused InkyPi plugin tests with the Python 3.12 `.venv-codex` interpreter plus `.pc-packages` on `PYTHONPATH`.

### Details
The default `python` and `.venv` currently use Python 3.14. The project-local `.pc-packages` contains `pytest`, but its Pillow binary extension does not load under Python 3.14 (`cannot import name '_imaging' from 'PIL'`). The 3.12 `.venv-codex` interpreter works with `.pc-packages`.

### Suggested Action
For focused InkyPi tests, use:

```powershell
$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONPATH='G:\PersonalProjects\EpaperSystem\inkypi-weather\package\InkyPi\.pc-packages'
.\.venv-codex\Scripts\python.exe -m pytest tests/test_backtothedate.py -q -p no:cacheprovider
```

Use `-p no:cacheprovider` to avoid unrelated `.pytest_cache` permission warnings.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/tests/test_backtothedate.py`, `inkypi-weather/package/InkyPi/.pc-packages/`
- Tags: inkypi, pytest, pillow, python312, pc-packages

---
## [LRN-20260529-002] environment

**Logged**: 2026-05-29T19:55:20-07:00
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
When pytest tmp/cache permissions fail, run focused InkyPi logic tests through a custom fixture shim instead of default pytest temp dirs.

### Details
For `test_gcd_comic_covers.py`, `.venv-test` plus `.pc-packages` on `PYTHONPATH` loaded the correct Python 3.12 dependencies. Standard pytest still failed before test calls because it could not scan or create default temp/cache directories such as `C:\Users\super\AppData\Local\Temp\pytest-of-LocalTest`, `C:\tmp\pytest-*`, and a workspace `--basetemp`. The test functions themselves passed when invoked with a tiny shim that supplied `tmp_path` under `G:\PersonalProjects\EpaperSystem\tmp\...` and a minimal `monkeypatch.setenv`.

### Suggested Action
If focused plugin tests fail during pytest setup with temp/cache `PermissionError`, first try:

```powershell
$env:PYTHONPATH = (Resolve-Path '.pc-packages').Path + ';' + (Resolve-Path 'src').Path
.\.venv-test\Scripts\python.exe -m pytest -p no:cacheprovider tests\test_name.py
```

If pytest still fails before running tests, use a temporary custom runner that imports the test module and supplies only the fixtures used by the target tests.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/tests/test_gcd_comic_covers.py`
- Tags: inkypi, pytest, permissions, tmp_path, pc-packages

---
## [LRN-20260530-001] environment

**Logged**: 2026-05-30
**Priority**: medium
**Status**: active
**Area**: epaper

### Summary
Do not use `python -m py_compile` for InkyPi source checks in this workspace; it can fail while writing `__pycache__`.

### Details
`python -m py_compile inkypi-weather/package/InkyPi/src/plugins/...` attempted to replace a `.pyc` under the plugin `__pycache__` directory and failed with `[WinError 5] Access is denied`. The source itself parsed correctly.

### Suggested Action
For a read-only syntax check, use:

```powershell
python -B -c "import ast,pathlib; ast.parse(pathlib.Path(r'inkypi-weather\package\InkyPi\src\plugins\epaper_pet\epaper_pet.py').read_text(encoding='utf-8'))"
```

For tests, continue using the Python 3.12 `.venv-codex` interpreter with `.pc-packages` on `PYTHONPATH`, `PYTHONDONTWRITEBYTECODE=1`, and `-p no:cacheprovider`.

### Metadata
- Source: implementation
- Related Files: `inkypi-weather/package/InkyPi/src/plugins/epaper_pet/epaper_pet.py`
- Tags: inkypi, python, pycache, permissions, syntax-check

---
## [LRN-20260530-002] environment

**Logged**: 2026-05-30
**Priority**: low
**Status**: active
**Area**: epaper

### Summary
Avoid piping PowerShell here-strings directly into Python stdin for temporary InkyPi render scripts.

### Details
`@' ... '@ | python -B -` produced a leading UTF-8 BOM on stdin in this Windows PowerShell session, causing Python to fail with `SyntaxError: invalid non-printable character U+FEFF` at the first line.

### Suggested Action
For quick render checks, prefer `python -B -c "..."` one-liners, an existing checked-in smoke tool, or an `apply_patch`-created temporary script that is deleted afterward.

### Metadata
- Source: implementation
- Related Files: `tools/smoke_epaper_pet.py`
- Tags: inkypi, python, powershell, bom, render-preview
