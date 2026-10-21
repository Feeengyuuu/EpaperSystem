# Project storage and backup rules

The user requires backups to remain inside this project's target directory and only one previous release to be retained. This applies to the Windows workspace and the live e-paper device.

- Keep Windows release backups in `G:\PersonalProjects\EpaperSystem\.backups\previous`. Retain exactly one previous-release archive plus its small identity/hash manifest. The current working checkout is not a second backup archive.
- Create worktrees, build staging, test temporary files and task evidence inside `G:\PersonalProjects\EpaperSystem`. Do not store project backup/quarantine folders in Windows system Temp, `G:\w`, sibling projects or unrelated directories. Set temporary-directory variables per task process when a tool otherwise writes outside the project.
- On the device, use the transactional release layout `/opt/inkypi/releases` with only `current` and one `previous` release. Do not create additional historical source backups in the home directory or `/var/tmp`.
- Transport ZIPs are temporary: remove them after the deployment reaches a terminal state and the required evidence is collected. Keep compact manifests, logs and selected acceptance images instead of full duplicate source archives or extracted release trees.
- After a task is completed, remove its obsolete release/source archives, extracted staging, redundant test environments and completed worktrees. Keep the shared working test environment while it is needed.
- Preserve unfinished code: never delete dirty or unmerged worktrees, unknown source changes, user files, keys, live configuration or active runtime caches to free space. Unverified source material belongs under `.retained-work/` until reviewed, not in a system Temp backup directory.
- Before deleting a worktree, verify its resolved absolute path, identity, clean status and that its commit is recoverable from main. Use `git -c core.longpaths=true worktree remove` on Windows. A failed removal may unregister the worktree while leaving files; inspect that state and use the saved explicit cleanup plan rather than a blind force command.
- The primary checkout `G:\PersonalProjects\EpaperSystem` stays on `main`, the single development base. Short-lived task worktrees go under `.worktrees/` and are removed once their work is merged. The repository is public: `.learnings/`, `outputs/`, `.claude/`, `.backups/` and `.retained-work/` are local-only and must never be force-added to commits.
- Use native PowerShell `Remove-Item` / `Move-Item` with `-LiteralPath` for Windows cleanup. Check absolute path containment before any recursive operation, and verify a cross-volume copy before deleting its original.
- Keep the original workspace changes and other unfinished tasks intact. Do not use broad `git clean`, `git reset`, blanket cache wipes or cross-shell deletion commands.

## Device release rounds (`tools/release`)

Deploy only with the tracked toolkit in `tools/release`; do not copy or hand-edit deployment scripts in task folders. Each round writes its evidence to a new folder directly under `outputs/` (git-ignored) and never reuses a folder that already has `deployment.json`. Run it from the primary checkout on `main` with the reviewed source committed, using a Python 3.11 environment that has `requests` and Pillow. Relative paths are taken from the repository root.

```powershell
$Py = '.tmp\venvs\<release-venv>\Scripts\python.exe'; $Task = 'outputs\deploy-YYYYMMDD'
pwsh -NoProfile -File tools\release\run_test_shards.ps1 -TaskDir $Task -Python $Py
& $Py tools\release\epaper_release.py baseline --task-dir $Task
& $Py tools\release\epaper_release.py build --task-dir $Task
& $Py tools\release\epaper_release.py backup --task-dir $Task
& $Py tools\release\epaper_release.py deploy --task-dir $Task --release-label <name> [--execute]
& $Py tools\release\epaper_release.py health-gate --task-dir $Task
& $Py tools\release\epaper_release.py rebaseline-config --task-dir $Task --reason "<why>" --migration <runtime_migrations key>  # only if the release migrates saved settings
& $Py tools\release\epaper_release.py snapshot --task-dir $Task
& $Py tools\release\epaper_release.py show-plugins --task-dir $Task <plugin_id> [...]
& $Py tools\release\epaper_release.py journal --task-dir $Task
& $Py tools\release\epaper_release.py rotate-backup --task-dir $Task --acceptance-plugin <plugin_id> [--execute]
```

- `baseline` reads the live release over SSH and takes its expected source from the `current-manifest.json` of the round whose `deployment.json` deployed it (`--base-manifest` overrides). It records `preflight.json` and `health-predeploy.json`.
- `deploy` and `rotate-backup` are dry runs without `--execute`. `deploy` uploads 16 MiB parts with per-part sha256 and pauses, because a single large scp once froze the device's Wi-Fi, then runs `inkypi-update`; the release id is `deploy-<UTC>-<label>-<commit8>`. The device IPv4 is resolved from `ColoredEpaperFrame.local` per run and never stored; host keys stay pinned through `.tmp/epaperpod_known_hosts`.
- Acceptance refuses any change to the saved configuration. When the release itself migrates saved settings at startup, verify the live diff first, then run `rebaseline-config`: it accepts only settings hashes (not cycle or display control), needs the migration's `runtime_migrations` marker on the device and keeps the original identity in `configuration-rebaseline.json`. The instance count may change only for a migration that adds instances: pass `--added-plugins <plugin_id> ...`, and it then requires exactly one new instance per named plugin and every other instance unchanged.
- `show-plugins` saves `acceptance-<plugin>-shown.json` per hardware write; `rotate-backup` uses that proof to replace the single archive and manifest in `.backups/previous`. `snapshot` (label `postdeploy` by default) also checks the deployed files and the preserved configuration; `watch`, `probe` and `health-record` are available for extra checks.
- `deploy --execute` and `show-plugins` change the device: run them only in a round the owner authorized, never from cloud tasks. After the round, remove `.tmp/release/<task>` and keep only compact evidence in the task folder.

## Repository and cloud development

- The Python application, plugins and tests are in `inkypi-weather/package/InkyPi`; shared repository tools are in `tools/`. The Worker projects are `cloudflare/vehicle-auth-bridge` and `cloudflare/brief-reader`.
- Use Python **3.11** and Node **24.12.0**, matching `.github/workflows/test.yml`. Install the hash-locked development requirements, which exclude Raspberry Pi hardware dependencies. Do not change dependency locks to work around using the wrong interpreter.
- In Linux cloud checkouts, keep task scratch files, build staging and evidence inside the checkout. Before setup or tests in each new shell, run the following from anywhere inside the repository:

```bash
REPO_ROOT="$(git rev-parse --show-toplevel)"
mkdir -p "$REPO_ROOT/.tmp/codex-cloud"
export TMPDIR="$REPO_ROOT/.tmp/codex-cloud"
export TMP="$TMPDIR" TEMP="$TMPDIR"
export PYTHONDONTWRITEBYTECODE=1 PIP_DISABLE_PIP_VERSION_CHECK=1
```

- Initialize the existing development environment once; this requires package-download access:

```bash
PYTHON_BIN=python3.11
VENV_DIR="$REPO_ROOT/inkypi-weather/package/InkyPi/.venv"
source "$REPO_ROOT/inkypi-weather/package/InkyPi/scripts/venv.sh"
python -m pip check
node --version
```

- Shell activation may not persist between cloud tasks. Use `PYTHON="$REPO_ROOT/inkypi-weather/package/InkyPi/.venv/bin/python"` for later commands. Run relevant tests first, then the appropriate CI checks. Full Python validation is:

```bash
PYTHON="$REPO_ROOT/inkypi-weather/package/InkyPi/.venv/bin/python"
cd "$REPO_ROOT/inkypi-weather/package/InkyPi"
"$PYTHON" -m pytest tests -q --no-header -p no:cacheprovider -o faulthandler_timeout=180
```

- From the repository root, run `git diff --check`, `"$PYTHON" -m ruff check --no-cache inkypi-weather/package/InkyPi/src inkypi-weather/package/InkyPi/tests tools/run_simulated_soak.py tools/verify_clean_archive.py tools/release`, and `"$PYTHON" tools/check_architecture.py` for relevant code changes. The clean-archive gate, `"$PYTHON" tools/verify_clean_archive.py --pytest-args="-q -o faulthandler_timeout=180"`, validates committed `HEAD`, not uncommitted changes; retain the project-local temporary environment above.
- Full Python tests require Node for JavaScript interaction regressions. Tests use fake hardware; cloud test success does not establish physical display acceptance.
- Cloud onboarding authorizes repository development, not a device deployment. Do not run the Raspberry Pi installer, connect to the live device, publish Workers, or change live configuration as part of cloud setup. Do not copy local `.env`, SSH keys, account sessions or runtime caches into Git or cloud onboarding files. Preserve unrelated and unfinished work.
- See `docs/codex-cloud.md` for environment creation and task handoff.
