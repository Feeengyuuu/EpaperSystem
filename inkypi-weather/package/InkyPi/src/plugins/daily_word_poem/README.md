# Daily Word & Quote sources

English definitions use Free Dictionary API first, then the supported Wiktionary
MediaWiki Action API. Each dictionary request has an eight-second budget, shares
the task's cancellation/deadline when present, and caps decoded JSON at256KiB.
Wiktionary requests are sequential, use maxlag=5, and identify this application.
Only matching English entries with a valid page revision and recognized part of
speech are accepted; examples and quotations nested inside definitions are not
republished as definitions. Pronunciation comes only from a Pronunciation section.

Source-backed definitions are cached by word in one atomic `dictionary.json`
file: at most128entries and512KiB, with30-day freshness and365-day last-good
retention. Cache hits keep their original fetch timestamp. Force refresh bypasses
freshness reuse. If both sources fail, expired last-good definitions may remain
visible with `definition cached`, but they do not advance DATA success or replace
the last-good daily source payload. Without a usable source cache, the local
current-day page remains displayable with honest degraded provenance.

Successful Wiktionary data includes a stable revision URL, source revision,
attribution to Wiktionary contributors, and CC BY-SA4.0 license metadata. The
rendered footer identifies the contributors/license; the daily cache and shared
context preserve the source URL for attribution and inspection. Definitions are
normalized and the display may wrap or fit text to the available panel.

References:

- https://dictionaryapi.dev/
- https://www.mediawiki.org/wiki/API:Parsing_wikitext
- https://www.mediawiki.org/wiki/API:Etiquette
- https://en.wiktionary.org/wiki/Wiktionary:Copyrights
- https://creativecommons.org/licenses/by-sa/4.0/

This failover does not repair or guarantee the third-party primary server's
availability. A successful source refresh and a successful hardware display job
are separate acceptance checks; useful offline pixels are not proof of live data.
