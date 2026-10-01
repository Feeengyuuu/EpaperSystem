## [LRN-20260930-NASA-APOD] official provider migration

**Logged**: 2026-09-30
**Priority**: high
**Status**: resolved
**Area**: providers

### Summary
Use NASA's migrated official APOD API and validate its content identity, not only HTTP success.

### Evidence and change
NASA's apod-api repository announced its final update on 2026-09-10 and points to the NASA Science WordPress endpoint `/wp-json/wp/v2/apod-basic/{yymmdd}`. The official proxy copies `hdurl` into `url`; the direct endpoint's original `url` is an article permalink. A device probe of the legacy route returned HTTP 200 with the wrong title, NASA Science, while the direct official response identified the requested 2026-09-30 Arp 78 image. An HTTP 200 response alone is not proof of correct APOD data.

The plugin now requests the direct official endpoint without sending a NASA key, normalizes HTML title/explanation/credit into visible plain text, excludes article URLs from media candidates, and maps iframe records to the existing video fallback contract. NASA_SECRET remains required for DONKI. The returned record date must equal the requested date: the real September 30 image uses an October asset directory, which is not a publication-date identity. NASA-host validation, sensitive-query rejection, bounded transport, cancellation, same-date cache identity, and current-cycle NOAA core admission remain in force. There is no legacy network fallback or relaxation of freshness to hide failures.

### Validation
The APOD, core-admission, shared safe-image, plugin-resource and HTTP-contract suite passed locally under the shared Python 3.11 environment. Tests include the captured WordPress field shape, date mismatches, missing safe media, HTML/entity handling, iframe fallback cache compatibility, sanitized error categories and cancellation. Device recovery and display acceptance are recorded separately in the task report; local validation is not live-display proof.

### References
- https://github.com/nasa/apod-api/blob/master/README.md
- https://github.com/nasa/apod-api/blob/master/application.py
- https://science.nasa.gov/wp-json/wp/v2/apod-basic/260930
- Task evidence: outputs/nasa-recovery-20260930/

### CI follow-up
The initial focused suite omitted the shared `test_network_failure_regression.py`. Clean-archive CI exposed 13 failures because its fake providers still read a `params.date` query and asserted the old bare endpoint. The complete file reproduced the same 13 failures locally; production code was not changed to accommodate stale mocks. Its fake providers now validate the official date-suffixed route, no query credentials, and disabled redirects, while all original cancellation, transaction, fallback, cache reuse and zero-network assertions remain. The combined APOD, official API, core admission, network failure, safe-image, resource and HTTP-contract suite then passed all 334 tests. Provider migrations require a search across the entire test tree for request mock contracts, followed by the shared network regression suite, rather than only plugin-specific tests.
