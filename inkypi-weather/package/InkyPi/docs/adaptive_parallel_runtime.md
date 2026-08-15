# Adaptive parallel runtime

## Decision

InkyPi keeps exactly one `RefreshTask` consumer and one owner for cache,
configuration, receipt, rotation, and display commits. CPU parallelism is
limited to audited, immutable image preparation inside one killable child
process. The child uses a bounded Pillow thread pool; it never runs provider
fetches and never publishes canonical state.

This design uses the Pi Zero 2 W's cores without turning each refresh into an
independent worker. Multiple refresh consumers are intentionally forbidden:
the queue coalescing and instance revision checks rely on a single ordered
commit stream.

One scheduler turn may fast-follow at most one additional reviewed inline
DATA job (two jobs total by default, configurable only within a 1-4 bound).
The follow-up is selected only after the prior job has reached a successful
terminal state, so its runtime receipt has already made that exact identity no
longer due. Queue work is probed first on every loop: manual/display work
preempts and cancels the remaining follow-up budget, and a retained Ian request
prevents the budget from being armed. Heavy, live, presentation, theme, and
unknown-plugin commands never enter this fast-follow path.

## Public runtime seams

```python
RuntimeResourceGovernor.acquire(kind, claim, context) -> ResourceLease

BoundedParallelStageRunner.run(
    workset: ImmutableImageWorkset,
    context: TaskContext,
    identity_validator,
) -> tuple[PreparedImageArtifact, ...]
```

`RuntimeResourceGovernor` reads the effective cgroup CPU quota together with
`MemAvailable` and swap use. Resource samples are fail-closed. An invalid,
missing, or non-finite sample selects the existing one-worker serial path.

`BoundedParallelStageRunner` accepts only immutable primitive descriptors and
one instance identity. A parent-owned staging directory is the only writable
child boundary. Each returned artifact is accepted only after the parent has
verified all of the following:

- the path is absolute, directly below the staging root, and contains no
  symlink or reparse escape;
- the target is an ordinary bounded file;
- the byte count and SHA-256 match the result descriptor;
- the format is PNG and the decoded dimensions and pixel count are bounded;
- the instance UUID, structural generation, and settings revision are still
  current.

Artifacts are returned in descriptor ordinal order, never completion order.
The parent performs final composition and serial publication. Cancellation,
deadline expiry, hard resource pressure, child failure, or stale identity
terminates and reaps the complete child process and discards its staging
artifacts.

## Adaptive tiers

The caller can request at most three image workers. The governor selects:

| Tier | Admission requirements | Behavior |
| --- | --- | --- |
| 3 workers | `MemAvailable >= 170 MiB`, swap `< 60%`, effective CPU quota `>= 300%` | one child, three Pillow threads |
| 2 workers | `MemAvailable >= 150 MiB`, swap `< 65%`, effective CPU quota `>= 200%` | one child, two Pillow threads |
| 1 worker | any lower or unknown resource state | original serial path |

Only one parallel image batch may exist across all governor instances. During
a running batch, soft pressure stops dispatching new items. Dispatch resumes
only after the resource sample becomes safe again. `MemAvailable < 70 MiB` or
swap `>= 75%` cancels and reaps the child. Swap `>= 70%` prevents additional
item dispatch. A serial fallback is useful work, not a refresh failure.

The initial production release keeps `CPUQuota=100%`. Headless activation is a
separate, transactional host migration. After headless verification, a second
release sets `CPUQuota=200%`; only a measured 24-hour success may authorize a
later `300%` release. `400%` is not a production tier.

## Global ownership and capacities

| Resource | Capacity | Ownership rule |
| --- | ---: | --- |
| parallel Pillow batch | 1 | one child with 1-3 threads |
| Chromium | 1 | central parent permit plus the existing process-local browser guard |
| AI generation | 1 | one shared permit across both AI image plugins |
| heavy child | 1 | every `LongTaskExecutor` holds one parent-owned permit until its complete process tree is reaped |
| display transaction | 1 | the app-owned transaction lock is the sole panel/SPI owner |
| provider I/O | 4 total, 1 per host by default | shared parent transport token, mutually exclusive with a running child provider lane |

Provider calls, browser work, AI generation, and canonical publication are not
valid image-stage transforms. Network results must first become validated,
parent-owned media records. Only local decode, EXIF normalization, color
conversion, resize/crop, enhancement, and PNG encoding may be admitted to the
parallel image stage.

Weather declares `CHROMIUM` on its `LongTaskExecutor.submit` call. The parent
therefore holds both the one heavy-child permit and the one Chromium permit
before the Weather process starts; the child-local semaphore is only a second
defense. Any future long-task child that intends to launch Chromium must add the
same explicit parent claim. No current raw process-spawn path other than the
audited long-task and bounded-image executors may launch a browser.

Every long-task child also holds an internal parent-side
`PROVIDER_IO_EXCLUSIVE` permit after `HEAVY_CHILD` admission and before an
optional `CHROMIUM` permit. It excludes parent `PROVIDER_IO` leases for the
child lifetime; the one-heavy-child limit then makes the spawned process's own
4-total/1-per-host registry the machine-wide provider bound. The only optional
child resource currently allowed is `CHROMIUM`. In particular,
`AI_GENERATION` is rejected as an optional child claim so the existing
AI-generation-then-heavy-child path cannot be paired with an inverse lock
order. Current synchronous long-task call sites do not hold a parent provider
lease while submitting or waiting; new call sites must preserve that rule.

On POSIX, the child creates one session before plugin code runs. A nested
`BrowserRenderer` remains in that session instead of detaching Chromium into a
second session. The child session leader remains alive after sending any
terminal message, closing the PID/PGID reuse window until the parent owns
cleanup. Before publishing that result, including a provider failure, the
parent terminates and verifies the complete process group. If the direct child
or any descendant survives TERM plus KILL, the result becomes
`child_process_leaked`; the process stays visible in active state and all
parent capacity leases are quarantined rather than reused. The refresh thread
receives a bounded failure and does not wait forever.

Display writes do not take a second governor lock: the sole app-owned
`DisplayTransaction` already holds one re-entrant lock across validation,
hardware I/O, manifest publication, and runtime-state commit, while the one
`RefreshTask` consumer remains the only canonical submitter. A concurrency test
guards this invariant.

## Plugin execution classes

Every registered plugin has one explicit class. A newly registered, unknown
plugin defaults to the serial inline path until reviewed.

### Parallel-image eligible

`ai_ecosystem_pulse`, `backtothedate`, `bambu_monitor`,
`box_office_top_movies`, `china_box_office_top_movies`, `comic`, `daily_art`,
`daily_knowledge`, `daily_wiki_page`, `daily_word_poem`,
`dota_profile_dashboard`, `flight_radar`, `gcd_comic_covers`, `image_album`,
`image_folder`, `image_upload`, `image_url`, `live_radar`, `lol_info`,
`magazine_covers`, `natgeo_photo_of_the_day`, `orbital_signal`,
`pixiv_r18_ranking`, `reddit_rule34_hot`,
`steam_daily_art`, `steam_profile_dashboard`, `unsplash`, `us_tv_hot_shows`,
`vehicle_status`, `wow_profile_dashboard`, and `wpotd`.

Eligibility is not automatic execution. A plugin must call the audited runtime
seam with an immutable local-media workset. The first canary adapters are
Magazine Covers, GCD Comic Covers, and Pixiv R18 Ranking. Daily Art and
Backtothedate are the second rollout batch. Simple Calendar remains inline
because it has no beneficial multi-item local stage; Species Radar remains
serial because its optional related-photo lane must skip individual corrupt
records rather than fail the entire presentation.

### Nested provider I/O

`apod`, `daily_ai_news`, `steam_charts`, and `telegram_digest` use the central
provider limit. They do not enter the Pillow batch merely because they use
threads or async I/O.

### Serialized heavy

`ai_image`, `ai_image_multiverse`, `ai_text`, `calendar`, `countdown`,
`epaper_pet`, `github`, `mini_weather`, `newspaper`, `rss`, `screenshot`,
`sports_dashboard`, `stocktracker`, `tech_pulse`, `ticketmaster_events`,
`todo_list`, `weather`, and `year_progress` remain on the serialized heavy
path. In particular, parallel batches never overlap multiple Chromium
instances.

### Inline light

`chinese_literature_clock`, `clock`, `flow_progress`, `literature_clock`,
`moon_phase`, `simple_calendar`, and `species_radar` remain inline. Splitting
their work either adds more process cost than it removes or weakens existing
per-asset failure isolation.

## Prepared-bank presentation

Magazine Covers and GCD Comic Covers attest
`presentation_refresh_is_provider_free=true`. This capability permits their
already-warm local prepared bank to choose and render the next presentation
when the global provider-refresh switch is off. The attestation does not permit
network access. An un-attested plugin remains fail-closed.

Selection, pending state, source provenance, and presentation receipts remain
parent-owned and serialized. The parallel stage may prepare selected media,
but a result cannot advance the bank. Given the same bank, seed, settings, and
request receipt, the serial and parallel paths must produce identical pixels
and the same selection state.

## Health snapshot

Authorized detailed health exposes only aggregate parallel state:

- selected worker tier and serial-degradation reason;
- effective CPU quota and last admission memory/swap sample;
- last batch latency and worker/thread count;
- child peak RSS, cancellation count, and active child count;
- process-lifetime admission counts by tier, serial fallback counts by a fixed
  reason whitelist, batch count and total latency, normalized output pixels,
  cumulative child RSS peak, and cumulative cancellations;
- cgroup CPU throttling counters when available.

No descriptor path, plugin instance UUID, source URL, settings, provider error
text, or content title is included. Public unauthenticated `/healthz` and
`/readyz` remain limited to release, boot, uptime, and readiness status.

## Trusted headless migration

`headless_mode_v1` is a two-release operation. The capability release upgrades
the installed updater without requesting host mutation. A later activation
release must carry both an allowlisted migration request and the
release-bound capability expectation produced by the already-installed
updater.

Before host mutation, the updater durably records the default target and the
LightDM enabled/active states, enters `applying_host_migration`, and executes
only fixed `/usr/bin/systemctl` arguments. It sets `multi-user.target`, disables
LightDM, and stops LightDM; it does not call `isolate`. Failure or recovery from
an incomplete phase restores the exact snapshot. `healthy` is still an
incomplete host-migration phase: only a durably recorded `committed` phase keeps
headless mode. A power loss between readiness and that commit therefore restores
the captured graphical state and the previous release.

## Promotion and rollback

The 2-worker tier must run for 24 hours and improve the audited image stage by
at least 20% without OOM, earlyoom, status-75 recovery, watchdog reset, service
restart, display regression, or new Weather/Sports starvation. Promotion to
three workers additionally requires at least 15% improvement over two.

Immediately restore `CPUQuota=100%` and one worker after any of these signals:

- sustained `MemAvailable < 90 MiB`;
- swap `>= 70%` for 15 seconds;
- temperature `>= 75 C` or a non-zero firmware throttling bit;
- OOM/earlyoom, status-75 recovery, watchdog reset, or service restart.

Headless rollback restores the captured graphical state. Removing the exact
Magazine Covers instance is a separate playlist rollback. The cache schema is
unchanged, so either runtime version can consume existing canonical images.
