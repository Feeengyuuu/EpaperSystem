# Scheduler wakeups and intentional waiting

The serial worker requests one normal admission pass after a command releases
capacity, including failures, cancellation and a retained IAN task yielding its
turn. This happens after cleanup and resource sampling. Queued manual work and
ready IAN continuations retain priority. A recheck request is not an admission
or a successful refresh.

Each selection pass collects future DATA, LIVE, presentation and theme retry
deadlines from the state it already reads. Scheduled refreshes use real local
time occurrences, including DST transitions. Resource spacing and quiet-window
expiry also bound the next poll. A deadline cannot postpone an earlier poll.
Already overdue but blocked work does not schedule a zero-time retry loop.
The normal 30-second poll remains a fallback for changes with no known deadline.

An ordinary provider failure keeps its lane retry and lets other eligible
instances proceed. Failure to persist retry/deferral state instead arms bounded
GLOBAL backoff; the same selection pass must not admit more work after that
internal failure. Failed refreshes do not become successful DATA updates.

HARD resource limits, SOFT spacing, serial execution, prepared request identity
checks and actual display-commit requirements are unchanged. Existing reserved
rotation protection still applies. When a displayable cache exists without a
reservation, the same 120-second window excludes new ordinary DATA/LIVE work
from SERIAL_HEAVY, NESTED_IO and unknown classes while reviewed image/inline
classes remain eligible. This additional guard is capped at half a configured
cycle so shorter playlists still admit heavy work after a display. Caches under
display or presentation backoff do not reserve this window. These classes are
not a duration guarantee: variable
provider latency and non-preemptible hardware writes can still delay work.

Acceptance measures actual provider starts/successes and source-labelled
hardware writes. A configured 300-second interval, healthy endpoint or wakeup
log alone does not prove five-minute freshness or full playlist coverage.
