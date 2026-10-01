## [LRN-20260930-URLLIB3] security release gate

**Logged**: 2026-09-30
**Priority**: high
**Status**: resolved
**Area**: dependencies

### Summary
Treat a newly failing dependency audit as a real release blocker and apply only the reviewed security dependency delta.

### Evidence and change
The North America poster repair's CI rejected urllib3 2.7.0 for CVE-2026-97687, CVE-2026-97688 and CVE-2026-97689. Upstream urllib3 2.8.0 fixes HTTPS proxy TLS configuration handling, unbounded chunk-size buffering, and the chunked Deflate streaming loop. The audit was not bypassed.

Updated only urllib3 from 2.7.0 to 2.8.0 in requirements-base.in and the base, Raspberry Pi runtime and development locks. Both published release artifacts were downloaded from the official PyPI file host and checked against PyPI's SHA256 metadata. All other lock content was verified unchanged; no dependency resolver or broad upgrade was run. Local validation uses a task-owned, no-dependencies overlay, leaving the shared test environment unchanged.

### References
- https://urllib3.readthedocs.io/en/2.8.0/changelog.html
- https://pypi.org/pypi/urllib3/2.8.0/json
- Compact CI failure, release artifact verification, lock delta and test evidence: outputs/north-america-posters-20260930/

### Follow-up rule
Allow the release builder only the individually reviewed lock changes, re-run HTTP and affected-plugin regressions with the new package, and wait for the final commit's security checks before activation. A security dependency fix is separate from proof of the poster repair on the device.

### Local validation
The verified urllib3 2.8.0 overlay passed 170 tests with one platform skip across the requirement contract, HTTP clients and movie source/cache/repair paths. Requests used that same overlay module. A separate import without the overlay still resolved urllib3 2.7.0 from the shared environment. The first task-only runner lacked a Windows multiprocessing main guard and recursively launched pytest in a worker; its failed log is retained, the runner was corrected, and the same suite then passed without product-code or test-timeout changes.
