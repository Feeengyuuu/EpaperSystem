# Runtime pressure recovery and asset observability

The user authorized overall optimization after the September 5 read-only audit,
continuing through tested deployment and actual-device acceptance. Start from
published and active commit c8026774f63325161ff6f0e273c34384e13736f5. Preserve the
dirty primary checkout, dependencies, configured intervals, source-freshness
gates and physical display transaction rules.

## Evidence and behavior boundaries

- The 01:20–14:09 natural window completed 152 write/sleep pairs. Weather had
  39 DATA starts, median interval 20.07 minutes and maximum 25.91 minutes.
- Four Sports worker cancellations occurred below 70 MiB available memory.
  Eleven APOD failures named NOAA scales admission; later DATA succeeded.
- Test at the existing scheduler decision/execution, asset loader, worker
  receipt and SpaceWeatherRepository boundaries. Use real pixel fixtures,
  bounded HTTP fakes, fake clocks/resource samples and actual cache namespaces.
- Require identical output pixels and meaningful reduction in cold-process
  peak memory. Keep the original full and partial failure records.
- Validate exact release/source identity, terminal fresh DATA and display jobs,
  current image commits, readiness and natural driver writes on the device.

## Changes and decisions

1. Explicitly close detached local title/logo images before resizing. Replace
   the GIL-held alpha pixel loop with a channel LUT and release both channel
   temporaries. The original generated NFL asset stays unchanged.
2. Re-sample Weather memory immediately after its existing maintenance step.
   If the ordinary browser margin is now available, admit in this turn. If
   the concession floor is lost, do not reserve an unproductive quiet window.
   Ordinary fairness, SOFT spacing, rotation guard and execution checks remain.
3. Opt APOD into mandatory NOAA validation immediately after core refresh,
   before optional wind/alerts/DONKI requests. Failures include bounded,
   redacted state, observation time and reason. Preserve the existing retry
   schedule: the available logs do not establish a safe reason to lengthen
   recovery latency. Repeated failed cycles now avoid optional requests.
4. Capture bounded asset counters per region and send them with its result.
   The parent logs one summary; no per-image persistent counter writes occur.
   Successful memory hits, negative hits, disk hits, completed downloads,
   payload bytes, invalid entries, failures and local decode attempts differ.
   Normal cache expiry is a miss, not corruption. Guard logs name the region.

## Measurement limits

Windows OS high-water working-set deltas establish loader improvements, not a
whole-device memory reduction. A Python sampling thread can miss a peak during
GIL-held pixel loops. Completed-region counters omit partial work of canceled
workers and cover the shared logo loaders, not every HTTP request in Sports.
Driver write/sleep logs and PNGs do not establish optical panel color/ghosting.

## Validation at implementation

Broad Sports/APOD/scheduler regression: 1,795 passed before final additional
counter/redaction/exception-path cases. Required final CI and deployment
evidence are recorded in the accompanying acceptance report after completion.
Architecture check: 446 files, zero violations at the first integrated gate.

Independent cold-process benchmarks of the production patch, three repeats
per actual bundled asset, preserve pixel hashes:

| Asset | Peak delta before/after MiB | Time before/after ms |
|---|---:|---:|
| NFL | 20.39 / 13.90 | 151 / 22 |
| World Cup | 23.16 / 19.51 | 218 / 21 |
| NBA | 32.50 / 18.24 | 126 / 23 |
