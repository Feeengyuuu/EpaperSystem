# NASAPics Space Weather Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Upgrade the existing `apod / NASAPics` plugin into the approved mirrored DailyWiki-style space-weather page while preserving the fixed left Kp block, filling the right photo frame without black bars, refreshing offscreen data every 30 minutes, and keeping physical display writes under the existing playlist/display contract.

**Architecture:** Keep `Apod.generate_image()` as the narrow orchestrator. Put NASA APOD selection, instance identity, media/translation cache decisions, and admission in `apod.py`; put NOAA/DONKI normalization and independent source caches in `space_weather.py`; put all fixed PIL geometry, font fitting, and the one final `ImageOps.fit()` cover crop in `apod_page.py`. Reuse the existing runtime `DATA_REFRESH -> DISPLAY_CACHE` path and existing canonical instance PNG; do not add a live, presentation, refresh-on-display, theme-redraw, or plugin-owned display lane.

**Tech Stack:** Python 3.11, Pillow, existing InkyPi `HttpClient`, `TaskContext`, `atomic_write_json`, `safe_open_image`, managed cache namespaces, pytest, PowerShell test/release tooling, and the existing transactional updater on `ColoredEpaperFrame`.

## Global Constraints

- Work only in the isolated `codex/nasapics-space-weather` worktree. Preserve every unrelated dirty file in the original checkout.
- Follow strict TDD for each production behavior: add the focused test, run it and capture the expected RED failure, implement the smallest complete behavior, then run the focused GREEN test. Commit each task only after its focused suite is green and output is clean.
- Keep the approved 800x480 geometry exact: header `(0,0,800,65)`, left panel `(0,65,368,480)`, divider `x=366..367`, and right content `x=368..799`.
- `_draw_kp_panel()` must always receive `(20,77,354,180)`. It must not accept or derive a cumulative `cursor_y`. Missing data stays in its fixed cell and does not shift later content.
- The other fixed left rectangles are G/R/S `(20,190,354,218)`, metrics `(20,228,354,318)`, probabilities `(20,326,354,364)`, alert `(20,374,354,414)`, and source `(20,456,354,476)`.
- Short captions use photo `(368,65,800,364)` and caption `(368,364,800,480)`. Only the shared horizontal boundary may move upward, within `y=300..364`; the left side and Kp rectangle never move.
- Decode with EXIF transpose, convert to RGB, and cover the final photo rectangle exactly once with `ImageOps.fit(source, photo_size, centering=(0.5,0.5))`. Do not pre-cover 800x480. Do not letterbox, pad, stretch, round, or overlay the NASA logo.
- Caption fitting is measured with real fonts. Chinese is 20 down to 16 px, at most 3 lines; English is 14 down to 11 px, at most 6 lines. The optional kicker may be hidden before the caption grows to 180 px. NASA's English title is never truncated or ellipsized. If the complete caption cannot fit at 180 px, reject the whole candidate and preserve the last-good PNG.
- Production default is today's APOD in the device timezone with `randomizeApod=false` and `customDate=""`. Compatibility random mode selects once per device natural day; manual force refresh does not re-roll. A custom-date change changes the selection fingerprint.
- Prefer APOD `url`; use `hdurl` only when the standard URL is absent, invalid, or too small for the final photo rectangle. A video day falls back to the nearest prior image within 7 days and uses that response's title/date/copyright/media URL consistently.
- A media decode failure may use a provisional historical image, but the current-day media is retried on each 30-minute data cadence until it succeeds. The already-downloaded fallback blob is not downloaded again.
- Translation order is OpenAI using `OPEN_AI_SECRET` or `OPENAI_API_KEY`, then Groq. Translate the title only, once per `APOD date + SHA-256(English title)`. If translation is unavailable and no valid matching-title cached translation exists, preserve the English title and render an explicit Chinese-unavailable message; never invent a translation.
- All source/state/translation/aggregate JSON lives under `cache_dir()/instances/<sha256(uuid:generation)>/`; selection state lives under the analogous `data_dir()/instances/<sha256(uuid:generation)>/`. Settings revision does not create a new directory and does not by itself invalidate a selection; the semantic selection fingerprint contains mode, device day/requested date, and custom date. Only media blobs are globally shared, keyed by SHA-256 of the media URL and owned by a managed cache namespace.
- Trust only `runtime.long_task_executor.current_instance_identity()`. Production generation fails closed if UUID or structural generation is missing. Tests and explicit preview calls may pass a named temporary namespace through an internal helper; settings fields are never trusted as identity.
- Each source has an independent atomic JSON envelope with `schema`, `endpoint`, `fetched_at_utc`, provider observed/issued/valid timestamps, normalized `payload`, and `raw_digest`. Read with bounded JSON helpers and write only with `utils.atomic_file.atomic_write_json`. A corrupt component is isolated; failure never overwrites any other last-good component.
- Mandatory core admission for a normal data refresh is: usable APOD plus live current-cycle NOAA scales and live current-cycle NOAA Kp. Optional wind, magnetic field, alerts, DONKI, and translation may use live/fresh cache or their approved unavailable presentation. Core failure raises a controlled exception before a candidate can replace the canonical PNG or advance `data.last_success_at`.
- Aggregate provenance is `LIVE`, `FRESH_CACHE`, `STALE_CACHE`, or `LOCAL_FALLBACK`. Any stale/local candidate sets `image.info["inkypi_skip_cache"] = True`; provenance alone is not an admission guard.
- Manifest contract is top-level `refresh_on_display:false` and all of `supports_presentation_refresh`, `supports_live_refresh`, and `supports_day_night_theme` false. Do not implement live or presentation hooks and do not add a second display command.
- Production cadence is `{"interval":1800}`. Background `DATA_REFRESH` can refresh the canonical offscreen PNG but cannot write hardware. Normal healthy rotation uses the existing `DATA_REFRESH -> DISPLAY_CACHE` sequence and produces exactly one physical display write. `Display Now` is provider-free and displays only the latest canonical PNG once; if no canonical PNG exists, it fails rather than rendering a provider-backed shell.
- Migration is an explicit, auditable, one-instance CAS against `DailyDoseOfDay / apod / NASAPics`, matching UUID, structural generation, and settings revision. Merge the complete existing settings and change only `randomizeApod=false`, `customDate=""`, `refreshOnDisplay=false`, and cadence `{"interval":1800}`. Other APOD instances and playlist ordering must be unchanged.
- Never log API keys, authorization headers, or signed URLs. Release from a clean tracked source artifact with LF-safe Unix entry points, never from `/opt/inkypi/current`.

---

## Task 1: Trusted instance namespace, daily selection, and APOD record cache

**Files:**

- Create: `inkypi-weather/package/InkyPi/tests/test_apod.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_network_failure_regression.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/apod.py`

**Required public/internal type-contract sketch:** The executable stub bodies below are non-copyable planning notation. Task implementation replaces every stub, and Task 8 scans production files for any survivor.

```python
@dataclass(frozen=True)
class ApodRecord:
    selection_key: str
    requested_device_date: str
    date: str
    media_type: str
    title_en: str
    title_zh: str | None
    translation_state: Literal["pending", "live", "fresh_cache", "unavailable"]
    explanation: str
    copyright: str | None
    url: str | None
    hdurl: str | None
    image_url: str | None
    image_cache_key: str | None
    fetched_at_utc: datetime
    source_state: Literal["live", "fresh_cache", "stale_cache", "unavailable"]
    warning: str | None


@dataclass(frozen=True)
class InstancePaths:
    cache: Path
    data: Path
    media: Path
    identity_key: str


@dataclass(frozen=True)
class ApodSelection:
    device_day: str
    mode: Literal["today", "random", "custom"]
    requested_date: str
    fingerprint: str
    resolved_record_date: str | None = None
    record_cache_key: str | None = None
    provisional: bool = False


def _instance_paths(
    plugin: "Apod", *, preview_namespace: str | None = None
) -> InstancePaths:
    """Return trusted UUID+generation paths, or a named temp preview namespace."""


def _selection_fingerprint(
    *, mode: str, device_day: date, requested_date: str, custom_date: str
) -> str:
    """Hash the exact mode/day/requested-date/custom-date selection contract."""


def _resolve_selection(
    *, settings: Mapping[str, Any], device_day: date, paths: InstancePaths,
    rng: random.Random
) -> ApodSelection:
    """Reuse today's atomic selection or choose and persist exactly once."""
```

- [ ] Add fixtures/fakes in `test_apod.py` for two trusted identities and deterministic local dates. Tests must assert cache/data paths differ by UUID or generation, remain the same across settings revision, and fail closed when the runtime identity is absent.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "instance_namespace or identity"`. Expected: import/attribute failures because instance namespace helpers do not exist.
- [ ] Implement `ApodRecord`, `InstancePaths`, identity hashing with `sha256(f"{uuid}:{generation}")`, explicit preview namespace handling for tests, directory creation, and fail-closed production behavior.
- [ ] Run GREEN with the same command. Expected: identity tests pass.
- [ ] Add RED tests for today's mode, custom date, compatibility random mode, same-day repeat, manual force-repeat, next-day re-roll, and exact fingerprint invalidation. Assert a settings-revision-only change neither changes the instance directory nor re-rolls selection, while mode/requested-date/custom-date changes invalidate the old selection. Assert the persisted selection JSON is atomic and contains mode, device day, selected APOD date, selection fingerprint, provisional flag, and record reference.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "selection or random or custom_date"`. Expected: missing selection behavior.
- [ ] Implement deterministic selection persistence under the instance data directory. Retain the five-candidate compatibility search only for a new random day; never re-roll an existing valid day selection.
- [ ] Adapt old network regressions so they preserve secret-redaction and unusable-candidate behavior without requiring a fresh random draw on every render.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_apod.py tests/test_network_failure_regression.py -q -k "apod or selection or random"`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/apod.py inkypi-weather/package/InkyPi/tests/test_apod.py inkypi-weather/package/InkyPi/tests/test_network_failure_regression.py && git commit -m "Add instance-safe APOD daily selection"`.

## Task 2: NOAA scales, Kp, wind, and per-source cache envelopes

**Files:**

- Create: `inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_scales.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_kp_objects.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_kp_rows.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_wind_speed.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_wind_mag.json`
- Modify: `inkypi-weather/package/InkyPi/tests/test_apod.py`

**Required type-contract sketch:** The executable stub bodies below are non-copyable planning notation. Task implementation replaces every stub.

```python
SourceState = Literal["live", "fresh_cache", "stale_cache", "unavailable"]


@dataclass(frozen=True)
class SourceEnvelope:
    schema: int
    endpoint: str
    fetched_at_utc: datetime
    observed_at_utc: datetime | None
    issued_at_utc: datetime | None
    valid_from_utc: datetime | None
    valid_until_utc: datetime | None
    payload: Mapping[str, Any]
    raw_digest: str


@dataclass(frozen=True)
class SourceResult:
    name: str
    state: SourceState
    envelope: SourceEnvelope | None
    error: str | None = None


def normalize_scales(raw: Mapping[str, Any], *, now_utc: datetime) -> Mapping[str, Any]:
    raise NotImplementedError("Task 2 supplies the fixture-driven normalizer")


def normalize_kp(raw: Sequence[Any], *, now_utc: datetime) -> Mapping[str, Any]:
    raise NotImplementedError("Task 2 supplies the fixture-driven normalizer")


def normalize_wind_speed(raw: Sequence[Mapping[str, Any]], *, now_utc: datetime) -> Mapping[str, Any]:
    raise NotImplementedError("Task 2 supplies the fixture-driven normalizer")


def normalize_wind_magnetic_field(raw: Sequence[Mapping[str, Any]], *, now_utc: datetime) -> Mapping[str, Any]:
    raise NotImplementedError("Task 2 supplies the fixture-driven normalizer")


class SpaceWeatherRepository:
    def __init__(self, *, cache_dir: Path, http: HttpClient):
        self.cache_dir = cache_dir
        self.http = http

    def refresh_core(
        self, *, now_utc: datetime, context: TaskContext | None
    ) -> tuple[SourceResult, SourceResult]:
        raise NotImplementedError("Task 2 supplies core endpoint refresh")

    def refresh_wind(
        self, *, now_utc: datetime, context: TaskContext | None
    ) -> tuple[SourceResult, SourceResult]:
        raise NotImplementedError("Task 2 supplies wind endpoint refresh")
```

- [ ] Add fixture-driven RED tests for scales keys `-1/0/1/2/3`, UTC parsing, key `0` current state, earliest valid forecast probability day, 0..100 validation, and provider-age freshness gates.
- [ ] Add Kp RED tests for object arrays and old header/row arrays. Freeze `now_utc`; assert current is the newest observed/estimated row with `time_tag <= now+5m`, predicted peak is only `now < time_tag <= now+48h`, and `noaa_scale` is preserved without rounding `kp=4.67`.
- [ ] Add wind/magnetic RED tests that choose the newest timestamp rather than index 0, retain separate timestamps when they differ by more than five minutes, and derive Bz direction only from the original sign.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "scales or kp or wind or magnetic"`. Expected: missing module/functions.
- [ ] Implement the pure normalizers and immutable envelopes. Parse naive provider timestamps as UTC. Store both provider time and fetch time.
- [ ] Implement independent endpoint requests with the shared bounded `HttpClient`, explicit timeout/cancellation context, SHA-256 raw digest, and one atomic file per source: `scales.json`, `kp.json`, `wind_speed.json`, and `wind_mag.json`.
- [ ] Enforce double freshness gates: scales fetch <=30m and provider age <=30m (diagnostic stale through 2h); Kp fetch <=30m and current 3h interval plus 30m grace (diagnostic stale through 6h); wind/mag fetch <=30m and observation <=30m (stale through 60m).
- [ ] Add tests showing an HTTP-200 frozen provider observation becomes stale; a failed source preserves its own last-good file; a corrupt JSON file is isolated and re-fetched; one source's failure never changes another file's bytes/mtime.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "scales or kp or wind or magnetic or envelope or corrupt"`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py inkypi-weather/package/InkyPi/tests/test_apod.py inkypi-weather/package/InkyPi/tests/fixtures/apod && git commit -m "Normalize and cache NOAA space weather"`.

## Task 3: NOAA alert folding, DONKI selection, and aggregate weather snapshot

**Files:**

- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/noaa_alerts.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/donki_flr.json`
- Create: `inkypi-weather/package/InkyPi/tests/fixtures/apod/donki_cme.json`
- Modify: `inkypi-weather/package/InkyPi/tests/test_apod.py`

**Required type-contract sketch:** The executable stub bodies below are non-copyable planning notation. Task implementation replaces every stub.

```python
AlertState = Literal["active", "confirmed_empty", "unavailable"]


@dataclass(frozen=True)
class SpaceWeatherSnapshot:
    fetched_at_utc: datetime
    oldest_core_observed_at_utc: datetime | None
    current_scales: Mapping[str, Any]
    current_kp: Mapping[str, Any]
    forecast_48h: Mapping[str, Any]
    solar_wind: Mapping[str, Any]
    magnetic_field: Mapping[str, Any]
    probabilities: Mapping[str, Any]
    scales: SourceResult
    kp: SourceResult
    wind_speed: SourceResult
    wind_magnetic: SourceResult
    alerts: SourceResult
    donki: SourceResult
    alert_state: AlertState
    alert: Mapping[str, Any] | None
    donki_event: Mapping[str, Any] | None
    sources: Mapping[str, SourceResult]
    errors: Sequence[str]
    aggregate_state: SourceProvenance


def fold_alerts(
    raw: Sequence[Mapping[str, Any]], *, now_utc: datetime
) -> tuple[AlertState, Mapping[str, Any] | None]:
    raise NotImplementedError("Task 3 supplies ordered NOAA alert folding")


def select_donki_event(
    *, flr: Sequence[Mapping[str, Any]], cme: Sequence[Mapping[str, Any]],
    now_utc: datetime
) -> Mapping[str, Any] | None:
    raise NotImplementedError("Task 3 supplies deterministic DONKI selection")


def refresh_space_weather(
    repository: SpaceWeatherRepository, *, nasa_api_key: str,
    now_utc: datetime, context: TaskContext | None
) -> SpaceWeatherSnapshot:
    raise NotImplementedError("Task 3 supplies aggregate refresh and provenance")
```

- [ ] Add alert RED tests that sort by `issue_datetime` ascending, parse product type/serial/severity/provider validity, fold cancel/extension/supersedes, keep Synoptic Period as event period only, set ALERT local display to issue+3h, set SUMMARY local display to issue+24h, set WATCH daily-table display end to the last forecast UTC day-end capped at issue+96h, reject WARNING without provider validity, ignore unknown text rather than guessing, and expire candidates by `now_utc`.
- [ ] Assert the strict three-state distinction and copy: a successful empty/folded-empty response is `confirmed_empty`; request failure without fresh cache is `unavailable`; live/fresh active warnings are `active`.
- [ ] Add DONKI RED tests for FLR today-1d..today and CME today-7d..today query windows, M5+/X filtering, nested `isMostAccurate` analyses, nested ENLIL Earth-impact evidence, ETA `now-6h..now+72h`, and deterministic single selection with Earth-impact CME before flare.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "alert or donki or aggregate"`.
- [ ] Implement folding and selection without keyword-inferred severity or Earth impact. Write `alerts.json` and `donki.json` independently. Use fresh alerts through 30m. Use DONKI in promotable candidates only through 60m; retain 60m..24h data solely for diagnostics and hide it from the rendered candidate.
- [ ] Implement aggregate provenance from values actually displayed. Core scales/Kp live plus optional live/fresh yields `LIVE`; all displayed fresh cache yields `FRESH_CACHE`; any displayed stale value yields `STALE_CACHE`; no provider value yields `LOCAL_FALLBACK`.
- [ ] Add tests showing optional failures do not fail core admission, while a current-cycle scales or Kp failure is reported as mandatory-core failure even if its diagnostic stale cache remains readable.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "alert or donki or aggregate or core"`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py inkypi-weather/package/InkyPi/tests/test_apod.py inkypi-weather/package/InkyPi/tests/fixtures/apod && git commit -m "Add alert and DONKI weather aggregation"`.

## Task 4: Fixed mirrored PIL page and full-bleed right photo

**Files:**

- Create: `inkypi-weather/package/InkyPi/src/plugins/apod/apod_page.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_apod.py`

**Required type-contract sketch and fixed geometry:** The executable stub body below is non-copyable planning notation. Task implementation replaces it.

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from plugins.apod.apod import ApodRecord

HEADER_RECT = (0, 0, 800, 65)
LEFT_RECT = (0, 65, 368, 480)
KP_RECT = (20, 77, 354, 180)
GRS_RECT = (20, 190, 354, 218)
METRICS_RECT = (20, 228, 354, 318)
PROBABILITIES_RECT = (20, 326, 354, 364)
ALERT_RECT = (20, 374, 354, 414)
SOURCE_RECT = (20, 456, 354, 476)
RIGHT_X = 368


class ApodPageLayoutError(ValueError):
    """Raised when complete bilingual metadata cannot fit the approved page."""


def fit_photo(source: Image.Image, photo_size: tuple[int, int]) -> Image.Image:
    transposed = ImageOps.exif_transpose(source)
    return ImageOps.fit(
        transposed.convert("RGB"), photo_size,
        method=Image.Resampling.LANCZOS, centering=(0.5, 0.5),
    )


def render_apod_page(
    *, apod: "ApodRecord", title_zh: str | None,
    translation_unavailable: bool, weather: SpaceWeatherSnapshot,
    source_image: Image.Image, rendered_at_utc: datetime,
    dimensions: tuple[int, int] = (800, 480),
) -> Image.Image:
    raise NotImplementedError("Task 4 supplies the fixed PIL renderer")
```

- [ ] Add RED layout tests for strict RGB 800x480; short caption boundary pixels (`y=363` photo, `y=364` caption); maximum caption boundary pixels (`y=299` photo, `y=300` caption); divider/right boundary (`x=367` left/divider, `x=368` right content).
- [ ] Add spy-based RED coverage that directly captures `_draw_kp_panel(draw, KP_RECT, snapshot, fonts)` and proves `KP_RECT` remains exact for short/long English, long Chinese, translation unavailable, missing copyright, missing weather fields, and maximum caption height.
- [ ] Add synthetic quadrant-color wide, tall, and EXIF-rotated images. Assert every pixel on all four photo edges comes from image content and the crop remains centered; do not infer letterboxing from black pixels in an astronomy photo.
- [ ] Add true-font RED tests for 300 English characters, 60 Chinese characters, and their combination. Assert complete strings are recoverably laid out within 3/6 line caps, or `ApodPageLayoutError` rejects the candidate. Assert no ellipsis and no text bbox beyond `x=354` on the left.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "page or layout or caption or cover or kp_rect"`.
- [ ] Implement font loading through existing Microsoft YaHei/Base UI helpers; fixed high-contrast light palette; measured wrapping; bounded font/caption search; left fixed cells; header; caption; and the single final photo cover.
- [ ] Preserve information density: current Kp/mode/current G state/48h peak; current G/R/S; wind speed; Bz direction/value; Bt; forecast G/Kp; three probabilities; highest-priority alert or unavailable/empty copy; NOAA source and true observation/cache time. Optional DONKI occupies the alert line only when NOAA has no higher-priority active item.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_apod.py -q -k "page or layout or caption or cover or kp_rect"`.
- [ ] Generate a local deterministic proof PNG from fixtures at `outputs/nasapics-space-weather-fixture.png`; inspect it visually and record dimensions/mode and edge pixel assertions in the task report. Do not commit the generated PNG.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/apod_page.py inkypi-weather/package/InkyPi/tests/test_apod.py && git commit -m "Render fixed mirrored NASAPics page"`.

## Task 5: APOD media, translation, orchestration, provenance, and admission

**Files:**

- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/apod.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/apod_page.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_apod.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_network_failure_regression.py`

**Required orchestration type-contract sketch:** The executable stub bodies below are non-copyable planning notation. Task implementation replaces every stub.

```python
def _fetch_apod_record(
    *, http: HttpClient, api_key: str, requested_date: str,
    context: TaskContext | None
) -> ApodRecord:
    raise NotImplementedError("Task 5 supplies bounded APOD retrieval")


def _resolve_image_record(
    *, requested: ApodRecord, fetch_for_date: Callable[[str], ApodRecord],
    max_prior_days: int = 7
) -> tuple[ApodRecord, bool]:
    """Return an image record and whether the choice is provisional."""


def _resolve_media_blob(
    *, plugin: "Apod", record: ApodRecord, paths: InstancePaths,
    minimum_size: tuple[int, int], context: TaskContext | None
) -> tuple[Path, str]:
    """Reuse/download a managed URL-hash blob, preferring valid standard media."""


def _decode_media_blob(
    *, blob_path: Path, photo_size: tuple[int, int]
) -> Image.Image:
    """Safely decode for the already measured final photo rectangle."""


def _translate_title(
    *, title: str, apod_date: str, paths: InstancePaths,
    device_config: Any, context: TaskContext | None
) -> tuple[str | None, bool]:
    raise NotImplementedError("Task 5 supplies provider fallback and caching")
```

- [ ] Add RED tests for standard URL first, HD fallback only on invalid/undersized standard media, URL-hash global media dedupe across two instances, bounded safe decode directly to final photo geometry, and no second full-screen crop.
- [ ] Add RED tests for video fallback within seven prior days and same-response metadata consistency. Add provisional decode-fallback tests showing current-day media retries every cadence, historical fallback blob is reused, and the candidate switches at most once when current media recovers.
- [ ] Add translation RED tests for `OPEN_AI_SECRET`, `OPENAI_API_KEY`, Groq fallback, title-only request, `APOD date + SHA-256(English title)` cache, no cross-title reuse, matching-title last-good fallback, unavailable copy, and secret-free errors/logs.
- [ ] Add orchestrator RED tests: same-day healthy weather refresh makes no APOD provider request and performs no media download, while decoding the already-cached blob is allowed to compose updated weather; current-cycle scales/Kp failure raises before render promotion; optional failures use approved fixed cells; stale/local provenance sets skip-cache; healthy output attaches `LIVE` provenance and writes context only after admission.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_apod.py tests/test_network_failure_regression.py -q -k "media or video or provisional or translation or orchestration or provenance or secret"`.
- [ ] Replace direct `requests.Session.get`/legacy full-screen loader calls with the shared bounded `HttpClient`. Persist APOD payload and translation atomically in the trusted instance namespace; persist media only in the managed global media namespace.
- [ ] Refactor `Apod.generate_image(settings, device_config)` into the ordered transaction: trusted paths -> device day/selection -> APOD record and managed media-blob resolution -> current-cycle weather refresh -> title translation -> measured caption/photo size -> one safe final-rectangle media decode/cover -> render -> provenance/admission -> context -> return image. Media probing may read bounded metadata to reject corrupt/undersized standard media, but it must not pre-cover a full-screen image.
- [ ] Remove the NASA logo overlay from page content. Keep `nasa_logo.png` on disk for compatibility but leave it unused.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_apod.py tests/test_network_failure_regression.py -q`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/apod.py inkypi-weather/package/InkyPi/src/plugins/apod/space_weather.py inkypi-weather/package/InkyPi/src/plugins/apod/apod_page.py inkypi-weather/package/InkyPi/tests/test_apod.py inkypi-weather/package/InkyPi/tests/test_network_failure_regression.py && git commit -m "Integrate NASAPics data and admission"`.

## Task 6: Settings, manifest, and refresh/runtime regression contract

**Files:**

- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/plugin-info.json`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/apod/settings.html`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_settings.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`

**Final manifest:**

```json
{
  "schema_version": 2,
  "refresh_on_display": false,
  "capabilities": {
    "supports_presentation_refresh": false,
    "supports_live_refresh": false,
    "supports_day_night_theme": false
  },
  "display_name": "NASA Astronomy Picture Of the Day",
  "id": "apod",
  "class": "Apod"
}
```

- [ ] Add RED tests asserting the exact APOD manifest booleans, no enabled theme contract, no presentation/live hooks, and instance `refreshOnDisplay=false` precedence.
- [ ] Add a settings round-trip RED test proving an absent/empty `customDate` remains `""` and the form does not synthesize today's date. The settings page must not write a hidden refresh-on-display true value.
- [ ] Add runtime RED scenarios for a NASAPics instance with `refresh={"interval":1800}`: not due before 1800 seconds; due at the boundary; healthy normal rotation queues/runs `DATA_REFRESH` then `DISPLAY_CACHE`; background data writes no hardware; display cache makes no provider call and writes hardware once.
- [ ] Add RED scenarios for mandatory-core failure and skip-cache: canonical PNG SHA-256, mtime, and runtime `data.last_success_at` stay byte-for-byte unchanged; first failure without canonical PNG produces no startup shell; failed/backoff candidate is skipped.
- [ ] Add RED scenarios showing sunrise/sunset does not queue THEME_REDRAW, no live or presentation command is scheduled, and ordinary `Display Now` with `request_presentation=false` is provider-free and makes one hardware write.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_plugin_manifest.py tests/test_plugin_settings.py tests/test_refresh_task.py -q -k "apod or nasapics"`.
- [ ] Modify only APOD manifest/settings and the narrow APOD test fixtures/fakes required by runtime tests. Do not redesign global scheduler semantics.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_plugin_manifest.py tests/test_plugin_settings.py tests/test_refresh_task.py -q -k "apod or nasapics"`.
- [ ] Run the whole touched boundary: `./tools/run_inkypi_tests.ps1 tests/test_apod.py tests/test_network_failure_regression.py tests/test_plugin_manifest.py tests/test_plugin_settings.py tests/test_refresh_task.py -q`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/src/plugins/apod/plugin-info.json inkypi-weather/package/InkyPi/src/plugins/apod/settings.html inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py inkypi-weather/package/InkyPi/tests/test_plugin_settings.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py && git commit -m "Disable autonomous NASAPics display refresh"`.

## Task 7: Exact one-instance production migration with CAS

**Files:**

- Create: `inkypi-weather/package/InkyPi/install/migrate_nasapics_instance.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_migrate_nasapics_instance.py`

**Required interface:**

```python
@dataclass(frozen=True)
class ExpectedNasapicsIdentity:
    instance_uuid: str
    structural_generation: int
    settings_revision: int


@dataclass(frozen=True)
class NasapicsMigrationResult:
    playlist_name: str
    before: PluginInstanceSnapshot
    after: PluginInstanceSnapshot


def migrate_nasapics_instance(
    config: Config, *, expected: ExpectedNasapicsIdentity,
    playlist_name: str = "DailyDoseOfDay",
    plugin_id: str = "apod",
    instance_name: str = "NASAPics",
) -> NasapicsMigrationResult:
    """Perform one exact merge/CAS update and persist through Config.write_config()."""
```

- [ ] Add RED tests for exactly one target, zero/multiple target rejection, UUID/generation/revision mismatch rejection, merge preservation of unknown settings, only the three approved setting changes, exact interval cadence, revision increment, other APOD instances unchanged, and playlist order unchanged.
- [ ] Add a simulated config-store conflict test proving persistence fails without silently rebasing a stale model snapshot.
- [ ] Run RED: `./tools/run_inkypi_tests.ps1 tests/test_migrate_nasapics_instance.py -q`. Expected: migration module missing.
- [ ] Implement the function with `resolve_plugin_instance_snapshot()` and `update_plugin_instance_atomic()`, deep-copy/merge the complete settings, call `Config.write_config()`, and re-read the snapshot/order before returning. The CLI requires explicit `--expected-uuid`, `--expected-generation`, and `--expected-settings-revision`; it prints sanitized JSON before/after and exits nonzero on any mismatch.
- [ ] Run GREEN: `./tools/run_inkypi_tests.ps1 tests/test_migrate_nasapics_instance.py -q`.
- [ ] Commit: `git add inkypi-weather/package/InkyPi/install/migrate_nasapics_instance.py inkypi-weather/package/InkyPi/tests/test_migrate_nasapics_instance.py && git commit -m "Add exact NASAPics production migration"`.

## Task 8: Whole-branch verification, clean release, live migration, and physical proof

**Files:**

- Modify only if tests expose a task-scoped defect: files already named in Tasks 1-7
- Create locally but do not commit: `.superpowers/sdd/final-review.md`, `outputs/nasapics-space-weather-live.png`, and deployment evidence logs with secrets redacted

- [ ] Run repository hygiene checks:

```powershell
git status --short
git diff --check
$placeholderPattern = @('TO' + 'DO', 'T' + 'BD', 'FIX' + 'ME', 'X' + 'XX') -join '|'
Select-String -Path docs/superpowers/plans/2026-07-22-nasapics-space-weather.md -Pattern $placeholderPattern
$stubPattern = 'Not' + 'ImplementedError'
Select-String -Path inkypi-weather/package/InkyPi/src/plugins/apod/*.py,inkypi-weather/package/InkyPi/install/migrate_nasapics_instance.py -Pattern $stubPattern
```

Expected: only intentional feature-branch files are present; `git diff --check` emits no output; placeholder and production-stub scans emit no output.

- [ ] Run focused and full tests from the isolated worktree:

```powershell
./tools/run_inkypi_tests.ps1 tests/test_apod.py tests/test_migrate_nasapics_instance.py tests/test_network_failure_regression.py tests/test_plugin_manifest.py tests/test_plugin_settings.py tests/test_refresh_task.py -q
./tools/run_inkypi_tests.ps1 -q
```

Expected: all selected and full-suite tests pass with no unexpected warnings or collection errors.

- [ ] Generate a final fixture page and visually inspect it with an image viewer. Confirm: right photo reaches all four frame edges; no black letterbox/padding; bilingual caption remains below; the left Kp block matches `(20,77,354,180)`; all required left metrics are visible; no text crosses `x=354`; output is RGB 800x480.
- [ ] Request a whole-branch spec/code-quality review against commit `70ea0204`; fix every Critical/Important finding through RED/GREEN tests and re-review until approved.
- [ ] Build the release from the clean feature commit with the repository release builder. Run archive verification against the exact ZIP, including LF-only Unix scripts, no `.learnings`, `.git`, worktree metadata, runtime caches, credentials, or local output; record the ZIP SHA-256.
- [ ] Preflight the pinned device connection and baseline service state:

```powershell
./tools/epaperpod-test-key.ps1
C:\Windows\System32\curl.exe --noproxy '*' -sS -o NUL -w '%{http_code}\n' http://ColoredEpaperFrame.local/playlist
C:\Windows\System32\curl.exe --noproxy '*' -sSI http://ColoredEpaperFrame.local/api/current_image
```

Expected: key probe succeeds, playlist returns HTTP 200, and current image has a real `Last-Modified` header.

- [ ] Deploy the exact reviewed ZIP transactionally:

```powershell
$releaseZip = (Resolve-Path .\inkypi-release.zip).Path
$releaseSha = (Get-FileHash -LiteralPath $releaseZip -Algorithm SHA256).Hash.ToLowerInvariant()
$releaseId = "deploy-$([DateTime]::UtcNow.ToString('yyyyMMddTHHmmssZ'))-nasapics-$($releaseSha.Substring(0,12))"
./tools/epaperpod-deploy-zip.ps1 -ZipName $releaseZip -ReleaseId $releaseId
```

Expected: updater verifies the SHA-256, switches the release, and `inkypi.service` remains active.

- [ ] Over pinned SSH, read the exact `DailyDoseOfDay / apod / NASAPics` instance and record sanitized UUID, generation, revision, full settings keys, cadence, and neighbor order. Run `migrate_nasapics_instance.py` with those exact expected values. Re-read and assert only `randomizeApod=false`, `customDate=""`, `refreshOnDisplay=false`, and `refresh={"interval":1800}` changed; all other APOD instances and ordering are identical.
- [ ] Submit one cache-only data refresh through `POST /refresh_plugin_instance` using the target snapshot CAS fields. Poll the job to a successful terminal state. Do not count the submission HTTP response as success.
- [ ] Record target canonical PNG path, SHA-256, mtime, HTTP `Last-Modified`, and `latest_refresh_time`. Verify today's APOD record/media selection, core NOAA scales/Kp live state, and no repeated large APOD download in the refresh logs.
- [ ] Submit one separate cached display through `POST /display_plugin_instance` with `request_presentation=false`. Poll to successful terminal state and verify exactly one Waveshare/physical display commit; no live/presentation/theme/refresh-on-display follow-up is queued.
- [ ] Download `/api/current_image` to `outputs/nasapics-space-weather-live.png` and compare its SHA-256 to the displayed/instance image as appropriate for the committed transaction. Inspect the actual PNG visually for all approved geometry/data/caption requirements.
- [ ] Wait through or simulate another due 1800-second data refresh without a date change. Verify weather fetch timestamps advance, APOD selection/media hash stays constant, no extra hardware display occurs from background DATA alone, and the next normal playlist display uses one cached hardware write.
- [ ] Inspect redacted service/queue logs, `systemctl is-active inkypi.service`, resolved `/opt/inkypi/current`, restart counter, and memory warnings. Confirm no unexpected restart, provider-secret leakage, duplicate APOD downloads, second display, or stale-success timestamp.
- [ ] Append exact commands, job IDs, hashes, timestamps, release ID/SHA, and visual evidence path to `.superpowers/sdd/final-review.md`; do not place secrets or signed URLs in the report.

## Plan self-review gate

- [x] Coverage: map every approved design section 5-14 to at least one test or live proof above. Geometry maps to Task 4; APOD/translation to Tasks 1 and 5; NOAA/DONKI/cache to Tasks 2 and 3; refresh semantics to Task 6; migration/live proof to Tasks 7 and 8.
- [x] Interface consistency: `ApodRecord`, `InstancePaths`, `SourceEnvelope`, `SourceResult`, `SpaceWeatherSnapshot`, and the renderer/orchestrator signatures have one owner and do not form import cycles (`apod.py` owns APOD records, `space_weather.py` owns weather records, and `apod_page.py` uses a `TYPE_CHECKING`-only APOD type import while `apod.py` imports the renderer at runtime).
- [x] Scope: no DailyWiki edit, no global scheduler redesign, no new standalone space-weather playlist plugin, no OVATION/solar imagery, and no APOD explanation body on the page.
- [x] Placeholder scan and whitespace gate pass before plan commit:

```powershell
$placeholderPattern = @('TO' + 'DO', 'T' + 'BD', 'FIX' + 'ME', 'X' + 'XX') -join '|'
Select-String -Path docs/superpowers/plans/2026-07-22-nasapics-space-weather.md -Pattern $placeholderPattern
git diff --check
```
