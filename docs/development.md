# Development

The runnable Python app lives in `inkypi-weather/package/InkyPi`.

## Local Test Environment

Use one development virtual environment at `inkypi-weather/package/InkyPi/.venv`:

```powershell
cd inkypi-weather\package\InkyPi
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r install\requirements-dev.txt
```

`install/requirements-dev.txt` intentionally excludes Raspberry Pi hardware packages. The device runtime still installs `install/requirements.txt`, which adds `inky`, `cysystemd`, and `pi-heif` on top of the shared base requirements.

## Running Tests

The browser interaction regressions execute the shipped JavaScript with Node.js
24 and an in-memory DOM. Make `node` available on `PATH` before running the full
Python suite; these tests do not need npm packages, a browser, or device access.

From the repository root:

```powershell
.\tools\run_inkypi_tests.ps1 tests\test_http_client.py -q
.\tools\run_inkypi_tests.ps1 -q
```

The helper chooses the first Python environment that actually has pytest installed, sets `PYTHONPATH` for the InkyPi source tree, and writes pytest temporary files under `inkypi-weather/package/InkyPi/.tmp/pytest`.

For CI parity, run the same fatal-lint and pytest checks:

```powershell
.\tools\run_inkypi_tests.ps1 -q
cd inkypi-weather\package\InkyPi
.\.venv\Scripts\python.exe -m ruff check --no-cache src tests
.\.venv\Scripts\python.exe -m pytest tests -q --no-header -p no:cacheprovider
```

The full suite is large; on Windows, `tools/release/run_test_shards.ps1` runs it
in six independent processes and writes one log per shard:

```powershell
pwsh -NoProfile -File tools\release\run_test_shards.ps1 -TaskDir outputs\<task> -Python <python.exe>
```

## Device Releases

Deploy only with the tracked toolkit in `tools/release`, from the primary
checkout on `main` with the reviewed source committed. Each round writes its
evidence to a new folder directly under `outputs/` (git-ignored).

```powershell
$Py = '<python 3.11 with requests and Pillow>'; $Task = 'outputs\deploy-YYYYMMDD'
& $Py tools\release\epaper_release.py baseline --task-dir $Task      # live release, health before
& $Py tools\release\epaper_release.py build --task-dir $Task         # release archive from HEAD
& $Py tools\release\epaper_release.py backup --task-dir $Task        # verified previous release
& $Py tools\release\epaper_release.py deploy --task-dir $Task --release-label <name> [--execute]
& $Py tools\release\epaper_release.py health-gate --task-dir $Task   # readiness, no new failures
& $Py tools\release\epaper_release.py show-plugins --task-dir $Task <plugin_id> [...]
& $Py tools\release\epaper_release.py snapshot --task-dir $Task      # deployed files and config
& $Py tools\release\epaper_release.py journal --task-dir $Task       # errors since the deploy
& $Py tools\release\epaper_release.py rotate-backup --task-dir $Task --acceptance-plugin <plugin_id> [--execute]
```

`deploy` and `rotate-backup` are dry runs without `--execute`. `deploy` uploads
the archive in 16 MiB parts with per-part checksums and pauses (one large copy
once stalled the device's Wi-Fi), then installs it with `inkypi-update` as
`deploy-<UTC>-<label>-<commit8>`; the device keeps the new release and one
previous release. `show-plugins` forces a provider refresh before each
hardware write, so a plugin whose source is briefly unavailable can report a
degraded refresh; `probe` saves the latest renders read-only, and
`health-record` re-checks health later. See `AGENTS.md` for the storage and
cleanup rules.
