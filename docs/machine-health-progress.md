# Machine health and effective refresh progress

## What the signals mean

`/healthz` is process liveness. `/readyz` additionally reports progress degradation;
a responding HTTP server alone is not evidence that feeds or the panel advance.
Anonymous responses remain minimal. Administrator-authenticated health responses
include `components.scheduler.progress`, with aggregate counts and ages only.
No instance names, messages, settings, credentials, or error text are published.

The scheduler observes every active instance before its resource/admission gates.
It reuses CacheCatalog's file-identity validation cache; HTTP polling performs no
cache IO, provider calls, or rendering. Observation is bounded by active instances.

| Condition | Interpretation |
| --- | --- |
| DATA overdue | The configured interval or scheduled occurrence is due, even during retry backoff. |
| DATA stalled | Overdue for at least `max(900 seconds, 2 * refresh interval)`; scheduled-only feeds use 900 seconds after the due occurrence. |
| Never-successful/missing-cache work | A monotonic observation period prevents instant false alarms and prevents bootstrap age from resetting on every scan. |
| Presentation pending | Same revision, presentation enabled, and no matching commit receipt, including an old-theme prepared result. |
| Presentation stalled | Pending for `max(900 seconds, 2 * active instance count * rotation interval)`, allowing two full rotations. |
| Obsolete presentation | Disabled, wrong revision, or already receipted; diagnostic only, not a stall. |

Sustained DATA/presentation stalls produce `data_progress_stalled` and/or
`presentation_progress_stalled`, with `degraded` and HTTP 200. They do not request
a restart. Existing fatal checks retain `not_ready`/503 priority. Recovery clears
the corresponding aggregate on the next scheduler observation; DATA recovery
does not hide a still-uncommitted presentation. Invalid/future timestamps do not
manufacture historical stalls. In-memory bootstrap observation restarts when the
process restarts; historical valid success/request timestamps remain useful.

## Recovery guarantees and limits

A presentation deadline backs off the exact request and releases its rotation
reservation unless that request is already prepared for the **current** resolved
theme. A previous night's prepared image does not suppress today's failure.
Matching prepared results and replacement request identities remain protected.
Retries and cached-display fail-open use the existing scheduler, not an additional
competing watchdog. A source that stays unavailable cannot be made fresh by retries.

The updated transactional engine verifies the restored release's `.release-id`
against `/readyz` before declaring rollback complete or deleting backups.
Unready/wrong-release recovery remains retryable, with backups retained. An
originally inactive service remains inactive. Existing acceptance of `degraded`
is unchanged. Directory names are not used as release identity.

This gate applies when the **new engine is executing**. Installing its files does
not retroactively change the engine already performing that deployment. A first
upgrade that restores an older engine and then reboots still has the older boot
recovery behavior. Validate future upgrades with this release as the baseline;
do not claim destructive live rollback or power-loss testing from local tests.

## Read-only acceptance window

Compare timestamped samples over 24 hours: release and boot IDs, service restart
events, available RAM/swap, storage, temperature/throttling, health status, DATA
success timestamps, same-ID presentation requests/receipts, and display commits.
Use source-labelled Waveshare write/sleep events for natural rotation timing.
Manual refreshes and cache timestamps alone do not establish natural cadence.
Match a stable display manifest to current-image ETag and pixel hash. Driver-chain
evidence does not establish optical panel quality without a camera.

Keep monitoring read-only: no restart, refresh, configuration edits, dependency
installation, reboot, or fault injection. Report persistent degradation with its
evidence; do not repeatedly restart a healthy worker because a provider is down.

On the audited 448 MiB device, memory cgroups and PSI were unavailable. Use
`/proc/meminfo` as a fallback, distinguish per-activation `NRestarts` from journal
history, and watch dependency preparation overlap. Boot flags, sudo/helper trust,
network settings, and reboots require separate authorization. Journal retention
limits are ceilings, not a guarantee of a full week of evidence.

## Regression entry points

From `inkypi-weather/package/InkyPi`, with the project's Python environment:

```text
python -m pytest tests/test_machine_progress_soak.py tests/test_refresh_progress_integration.py tests/test_refresh_progress.py tests/test_health_snapshot.py tests/test_health_blueprint.py tests/test_update_transaction_health.py -q
```

The soak drives the real scheduler, queue, runtime state, cache and display
transaction across two rotations; only provider, clock, OS-resource and physical
panel boundaries are simulated. It includes old pending work, provider recovery,
and cross-theme preparation deadline failure. It is not an optical hardware test.
