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
