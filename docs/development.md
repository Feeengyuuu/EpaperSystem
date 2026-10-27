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

### Owner-provided private fonts

After a release containing this capability has successfully deployed, the owner
can install a separately licensed local sport/league score font. Supported keys
are `nba`, `wnba`, `mls`, `epl`, `laliga`, `bundesliga`, `seriea`, `ligue1` and `f1`. No font is
downloaded by the command, and a public URL alone does not establish a license.
Keep the original font under the ignored `outputs/` directory, never in Git.

```powershell
& $Py tools\release\epaper_release.py private-fonts --task-dir $Task --league nba --font outputs\private-fonts\owner-font.woff2
& $Py tools\release\epaper_release.py private-fonts --task-dir $Task --league nba --font outputs\private-fonts\owner-font.woff2 --execute
```

GameDeals has a separate registered purpose, `game_deals_price`; it is not a
sports league. Use `--purpose` instead of `--league` for the owner-provided
Banknote Regular TTF. This fixes the destination to
`/var/lib/inkypi/data/fonts/game-deals-price.ttf` and additionally requires exact
`Banknote` / `Regular` metadata and visible `0123456789$.,-` glyphs. No other
format or destination is accepted for this purpose.

```powershell
& $Py tools\release\epaper_release.py private-fonts --task-dir $Task --purpose game_deals_price --font 'outputs\private-fonts\Banknote Regular.ttf'
& $Py tools\release\epaper_release.py private-fonts --task-dir $Task --purpose game_deals_price --font 'outputs\private-fonts\Banknote Regular.ttf' --execute
```

The first command validates the local file and records a plan without contacting
the device. The second uses the pinned SSH connection and the existing trusted
`inkypi-update` sudo entry. The deployed updater must match the current release;
the round's full commit, release id and artifact checksum bind the request to a
committed deployment. No saved settings, source, cache, service state or sudoers
are changed. Installation uses the same update lock as a release transaction.

Only `sports-<supported-key>-score.{ttf|otf|woff|woff2}` and the registered purpose's fixed basename beneath
`/var/lib/inkypi/data/fonts` are allowed. The raw font must be at most 2 MiB and
Pillow must read its metadata, distinct digits and score separator on both the
workstation and device. Bytes travel through SSH stdin, with no transport file.
Some official webfonts omit the family name while retaining a style name; the
receipt records the missing family as empty, and the original bytes, format,
hash, glyph visibility and distinct digit checks still apply. A font with no
family or style metadata is refused. No conversion or inferred font name occurs.
The existing directory must be root:inkypi, mode 0750, and contain no conflicting
format for that league. Components and targets are checked without following
symlinks. A new root:inkypi, mode 0640 file is published atomically without
replacement; an identical existing font is a no-op, and any different existing
font is refused for owner review. A temporary file in that same directory is
removed on handled success or failure. Following a connection loss, retry the
same command to determine whether it installed before the connection ended.

The task retains `private-fonts-<league-or-purpose>-plan.json` and an append-only list of
attempt receipts in `private-fonts-<league-or-purpose>.json`, containing paths, hashes and
font metadata, never font bytes. Both archive builders explicitly exclude the
private basenames. Display acceptance still uses the normal `show-plugins` and
`snapshot` steps after installation; a font installation receipt alone is not
physical display proof.
