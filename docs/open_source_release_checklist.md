# Open Source Release Checklist

Run this before making the repository public.

## Secrets

Check ignored local files are not tracked:

```bash
git ls-files .env "**/.env" ".ssh/*" ".secrets-backup/*" ".tmp/*" "tmp/*"
```

If any secret file appears, stop and remove it from Git history before publishing.
Do not just delete it in a new commit.

Search for likely secrets in tracked files:

```bash
git grep -n -E "sk-|xox|ghp_|github_pat_|api[_-]?key|secret|token|password|passwd"
```

Review every hit. Some hits are expected variable names; real values are not OK.

## Local Runtime State

These should stay local and untracked:

```text
.env
.ssh/
.secrets-backup/
.learnings/
.tmp/
tmp/
**/cache/
**/__pycache__/
**/.pytest_cache/
```

## GitHub Metadata

Before publishing, update placeholders:

- `README.md` clone URL.
- `inkypi-weather/package/InkyPi/docs/install_from_zero.md` clone URL.
- GitHub issue links if you want them to point at the new project instead of upstream InkyPi.

## README Screenshots

- README plugin screenshots should come from a real device endpoint such as
  `/api/current_image` or `/plugin_instance_image`.
- Generated/img-2 imagery may be used for the surrounding scene or empty device
  frame, but not for the plugin screen content.
- Avoid publishing screenshots with private photos, financial holdings, tokens,
  or personal account details.

## Installer Smoke Test

On a clean Raspberry Pi or disposable SD card:

```bash
sudo bash install/bootstrap.sh --non-interactive
sudo reboot now
```

After reboot:

```bash
bash install/healthcheck.sh
```

## API Key UX

Confirm these work without real keys:

```bash
python3 install/configure_api_keys.py --list
python3 install/configure_api_keys.py --check
```

Open:

```text
http://<pi>/api-keys
```

Confirm real key values are masked and never displayed back to the browser.

## License And Attribution

- Keep the GPL-3.0 license from InkyPi.
- Keep font/icon attribution in `inkypi-weather/package/InkyPi/docs/attribution.md`.
- Verify any newly added assets have redistribution rights.
