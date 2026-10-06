# Runtime reliability and status

The runtime status page is available at `/status` and linked from the playlist.
Its `/api/runtime-status` endpoint requires the existing administrator session,
returns `Cache-Control: no-store`, and reads detached snapshots. Polling the page
does not start providers, renderers, or display jobs.

The scheduler samples the active playlist and bounded, known source records.
Weather and Vehicle Status expose source timestamps when a cache can be attributed
to exactly one configured instance. Unknown or ambiguous provenance stays unknown;
render success does not become a source timestamp. Steam Charts independently
reports missing store metadata language queries and available requested covers.
These media counters describe fetched render inputs, not physical display proof.
The display commit remains a separate observation.

Live acceptance also exposed Steam context writers passing a naive local time to
a cache API that interprets naive values as UTC. Both chart modes now write an
aware UTC instant. A non-UTC clock regression covers the stored timestamp and
freshness calculation; local display labels keep their existing timezone.

Plugins whose manifest owns refresh before display are excluded from periodic
DATA overdue health calculations. Display progress monitoring stays enabled.
Their saved refresh interval is retained, but is not presented as an effective
background fetch schedule. A failed attempt followed by a newer DATA success is
not shown as an active failure. Future deferred retries remain visible.

Automatic recovery requests append a bounded 32-event ledger at
`$INKYPI_DATA_DIR/supervised-recoveries.json`. It survives release switches. Events
start with this feature; ordinary deployment restarts are not counted or inferred.

Cooperative cancellation cannot stop plugin code that ignores its deadline. The
restart monitor therefore requests the same supervised replacement when the
active command runs more than `refresh_overrun_grace_seconds` (default 240,
bounded 60-3600) past its deadline, records `refresh_worker_overrun` in the
ledger, and arms the 15-second forced exit. A second overrun recovery within
`refresh_overrun_min_interval_seconds` (default 3600) is suppressed and only
logged, so a deterministic hang cannot become a restart loop. Set
`refresh_overrun_recovery_enabled` to `false` to disable it. Inside a bound
refresh task, the shared HTTP session and `HttpClient` calls without an explicit
context also inherit that task's cancellation and deadline.

Forced isolated-task cancellation no longer sets a multiprocessing event before
terminating the child. A child can own that event's semaphore indefinitely, so
setting it can block the parent and retain provider permits. The parent instead
uses the existing terminate/kill/reap path and preserves fail-closed quarantine
and supervised recovery when it cannot prove complete cleanup.

Sports cache JSON is streamed through the existing atomic fsync/replace protocol.
NBA calendar compaction preserves all fixtures and parser-used fields, removes
unused provider statistics, and keeps provenance times unchanged. The old merged
scoreboard object is released before acquiring another calendar graph. A real
device-cache comparison preserved all 1,185 parsed fixture records while reducing
serialized calendar size from 5,993,559 to 2,661,262 bytes.

Regression coverage includes child-owned cancellation locks, permit release,
failed streaming writes preserving the previous cache, display-owned freshness,
Weather deferral yielding to another cached page, media/source separation,
administrator access, bounded recovery history, and WNBA `unstarted` fixtures.
Long-duration natural operation must be evaluated separately from bounded tests
and deployment acceptance. The target remains at least 48 hours without a child
cleanup recovery, with display gaps assessed outside deployment and explicit
manual-pause windows.

Release auditing found five published advisories in the existing dependency
locks. AnyIO is pinned to 4.14.2 and Soup Sieve to 2.9, including PyPI SHA-256
hashes in the base, Pi, and development locks. No other package versions change.
Runtime and development locks must both pass the strict dependency audit, and
ARM64 distribution resolution and full clean-archive tests must pass before
release acceptance. These dependency advisories are separate from the confirmed
multiprocessing event-lock flaw described above.

Primary release notes:
- https://github.com/agronholm/anyio/releases/tag/4.14.2
- https://github.com/facelessuser/soupsieve/releases/tag/2.9
