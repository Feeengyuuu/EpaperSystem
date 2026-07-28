### Summary
When a narrow transactional release must use `/opt/inkypi/current` as its clean baseline, exclude the top-level `venv_inkypi` before building the candidate archive.

### Details
The active release's `install/lib/release_archive.py` excludes `.venv` variants, bytecode, runtime plugin caches, and the mutable current image, but it does not exclude the installed `venv_inkypi` directory. Running that archiver directly against `/opt/inkypi/current` therefore includes the live virtual environment. The updater then rejects the candidate because a prepared release must not already contain a virtual environment.

### Suggested Action
Create an isolated baseline stage with `tar -C "$current" --exclude='./venv_inkypi'`, verify the baseline source hash, overlay only the tested runtime file, and build the ZIP with the staged release's own `release_archive.py`. Gate the ZIP for member count, path safety, caches, bytecode, CRLF shebangs, and exact payload hashes before invoking the transactional updater.

### Metadata
- Source: deployment_incident
- Related Files: `inkypi-weather/package/InkyPi/install/lib/release_archive.py`, `inkypi-weather/package/InkyPi/install/lib/update_engine.py`, `tools/epaperpod-deploy-zip.ps1`
- Tags: inkypi, deployment, transactional-update, release-archive, venv
