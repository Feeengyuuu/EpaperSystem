# Plugin Resource Bounds Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve all plugin features while making plugin loading, HTTP, images, Chromium, long tasks, and caches lazy, bounded, cancellable, and observable.

**Architecture:** Put explicit capability metadata in manifest schema v2; centralize strict settings parsing and resource services under focused modules. Existing plugin APIs remain compatibility facades while high-risk paths migrate first: SportsDashboard, TechPulse, Ticketmaster, Screenshot, and AI Horde.

**Tech Stack:** Python 3.11, dataclasses, ast, requests/urllib3, Pillow, subprocess, multiprocessing, pytest.

## Global Constraints

- Runtime scheduler and TaskContext from the runtime plan are prerequisites.
- Operations plan Task 1 (`RuntimePaths`) is a prerequisite for CacheManager root ownership.
- Built-in plugin behavior must not change merely because a manifest field is added.
- Only `sports_dashboard` declares `supports_live_refresh=true`; all other current built-ins declare false.
- `refreshOnDisplay` resolution is instance explicit value, then manifest default, then false.
- HTTP retries only idempotent GET/HEAD once for connect errors or 429/502/503/504.
- Default image limits are 25 MiB download, 8192 pixels per side, and 8 million total pixels.
- Browser concurrency is 1 and AI/long-task concurrency is 1 with queue capacity 2 on Pi.
- Managed memory cache is 32 MiB globally; a single image cache is at most 128 entries/20 MiB.
- Managed disk cache is 50 MiB/256 files/30 days per plugin and 512 MiB globally.
- Existing uncommitted SportsDashboard, TechPulse, and Ticketmaster changes must be retained.

---

### Task 1: Introduce manifest schema v2 and lazy capability inspection

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py`
- Modify: `inkypi-weather/package/InkyPi/src/config.py:66-88`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/plugin_registry.py:19-94`
- Modify: all 57 `inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json`
- Create: `inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_registry.py`

**Interfaces:**
- Produces: `PluginCapabilities`, `PluginManifest`, `CapabilityCache`, `inspect_v1_capabilities()`.
- Consumes: standard-library AST and SHA-256 only; it must not import plugin modules.

- [ ] **Step 1: Write failing no-import and schema-contract tests**

```python
def test_v2_manifest_declares_live_refresh_without_import(tmp_path, monkeypatch):
    manifest_path = write_plugin(tmp_path, supports_live_refresh=True)
    imported = []
    monkeypatch.setattr(importlib, "import_module", lambda name: imported.append(name))
    manifest = PluginManifest.from_path(manifest_path)
    assert manifest.capabilities.supports_live_refresh is True
    assert imported == []


def test_all_builtin_manifests_are_v2_and_only_sports_is_live():
    manifests = load_builtin_manifests()
    assert all(item.schema_version == 2 for item in manifests)
    assert {item.id for item in manifests if item.capabilities.supports_live_refresh} == {"sports_dashboard"}
```

- [ ] **Step 2: Run tests and verify failure**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_manifest.py tests\test_plugin_registry.py`

Expected: FAIL because schema and capability classes do not exist.

- [ ] **Step 3: Implement manifest loading and v1 AST compatibility**

```python
@dataclass(frozen=True)
class PluginCapabilities:
    supports_live_refresh: bool = False


@dataclass(frozen=True)
class PluginManifest:
    schema_version: int
    id: str
    class_name: str
    display_name: str
    refresh_on_display: bool
    capabilities: PluginCapabilities
    raw: Mapping[str, Any]

    @classmethod
    def from_path(cls, path, *, capability_cache=None):
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        schema = int(payload.get("schema_version", 1))
        if schema == 2:
            capabilities = PluginCapabilities(
                supports_live_refresh=bool(payload.get("capabilities", {}).get("supports_live_refresh", False))
            )
        else:
            capabilities = inspect_v1_capabilities(Path(path).with_name(f"{payload['id']}.py"), capability_cache)
            logger.warning("Plugin %s uses manifest schema v1; migrate to v2", payload["id"])
        return cls(schema, payload["id"], payload["class"], payload["display_name"],
                   bool(payload.get("refresh_on_display", False)), capabilities,
                   MappingProxyType(payload))
```

`inspect_v1_capabilities()` parses class methods with `ast.parse`, caches by source SHA-256 in a sidecar capability cache, and never executes source.

- [ ] **Step 4: Migrate built-in JSON manifests mechanically**

Each manifest receives:

```json
"schema_version": 2,
"capabilities": {"supports_live_refresh": false}
```

Set only SportsDashboard to true. Preserve existing keys and JSON formatting as much as practical.

- [ ] **Step 5: Prevent live-refresh scan from loading ordinary plugins**

`Config.read_plugins_list()` returns raw compatibility dictionaries augmented with `_manifest`; `load_plugins()` stores metadata; `RefreshTask` checks manifest capability before calling `get_plugin_instance()`.

- [ ] **Step 6: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_manifest.py tests\test_plugin_registry.py tests\test_refresh_task.py -k "manifest or lazy or live_refresh"`

Expected: PASS and no ordinary plugin in `PLUGIN_CLASSES`/`sys.modules`.

```powershell
git add -- inkypi-weather/package/InkyPi/src/plugins/plugin_manifest.py inkypi-weather/package/InkyPi/src/config.py inkypi-weather/package/InkyPi/src/plugins/plugin_registry.py inkypi-weather/package/InkyPi/src/plugins/*/plugin-info.json inkypi-weather/package/InkyPi/tests/test_plugin_manifest.py inkypi-weather/package/InkyPi/tests/test_plugin_registry.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git commit -m "feat: declare plugin capabilities without eager imports"
```

### Task 2: Make refresh-on-display settings strict and instance-aware

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/plugins/plugin_settings.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py:54-60`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py:149-150`
- Create: `inkypi-weather/package/InkyPi/tests/test_plugin_settings.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_refresh_task.py`

**Interfaces:**
- Produces: `parse_strict_bool()` and `resolve_refresh_on_display()`.
- Consumes: manifest raw settings from Task 1.

- [ ] **Step 1: Write failing precedence tests**

```python
@pytest.mark.parametrize("value, expected", [(False, False), ("false", False), (True, True), ("true", True)])
def test_instance_value_overrides_manifest_default(value, expected):
    assert resolve_refresh_on_display(
        {"refreshOnDisplay": value}, {"refresh_on_display": True}
    ) is expected


def test_invalid_explicit_boolean_is_rejected():
    with pytest.raises(PluginSettingError):
        resolve_refresh_on_display({"refreshOnDisplay": "sometimes"}, {})
```

- [ ] **Step 2: Run and implement strict parsing**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_settings.py`

Expected: FAIL on missing module.

```python
def parse_strict_bool(value, *, field):
    if isinstance(value, bool):
        return value
    if isinstance(value, str) and value.strip().lower() in {"true", "false"}:
        return value.strip().lower() == "true"
    raise PluginSettingError(f"{field} must be true or false")


def resolve_refresh_on_display(settings, manifest, *, base_default=False):
    if "refreshOnDisplay" in (settings or {}):
        return parse_strict_bool(settings["refreshOnDisplay"], field="refreshOnDisplay")
    if "refresh_on_display" in (manifest or {}):
        return parse_strict_bool(manifest["refresh_on_display"], field="refresh_on_display")
    return bool(base_default)
```

- [ ] **Step 3: Route BasePlugin and Newspaper through the resolver**

BasePlugin calls `resolve_refresh_on_display(settings, self.config)`. Newspaper preserves its rotation-specific rule after checking an explicit instance value.

- [ ] **Step 4: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_settings.py tests\test_refresh_task.py -k refresh_on_display`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/plugins/plugin_settings.py inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py inkypi-weather/package/InkyPi/src/plugins/newspaper/newspaper.py inkypi-weather/package/InkyPi/tests/test_plugin_settings.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git commit -m "fix: honor instance refresh-on-display settings"
```

### Task 3: Bound shared HTTP behavior and response ownership

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/utils/http_client.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_http_client.py`
- Modify first-party direct callers: `ai_image_multiverse.py`, `daily_ai_news.py`, `daily_art.py`, `flight_radar.py`, `gcd_comic_covers.py`, `literature_clock/dataset.py`, `newspaper.py`, `steam_charts.py`, `utils/massive_market_data.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_http_client_contract.py`

**Interfaces:**
- Produces: `HttpClient.request_json()`, `request_bytes()`, `stream_to_file()`, `close_http_session()`.
- Consumes: runtime `TaskContext`.

- [ ] **Step 1: Write failing retry/deadline/size tests**

```python
def test_post_is_not_retried_and_get_retries_once_on_503(fake_adapter):
    client = HttpClient(session=fake_adapter.session([503, 200]))
    assert client.request_json("GET", "https://example.test", context=context()).status == 200
    assert fake_adapter.calls == 2
    fake_adapter.reset([503, 200])
    with pytest.raises(HttpStatusError):
        client.request_json("POST", "https://example.test", context=context(), json={})
    assert fake_adapter.calls == 1


def test_request_bytes_closes_response_when_limit_exceeded(fake_response):
    client = HttpClient(session=session_for(fake_response))
    with pytest.raises(ResponseTooLarge):
        client.request_bytes("GET", "https://example.test/image", max_bytes=4, context=context())
    assert fake_response.closed
```

- [ ] **Step 2: Run tests and implement the adapter**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_http_client.py tests\test_http_client_contract.py`

Expected: FAIL because current retry count is 3 and callers receive raw responses.

Use urllib3 `Retry(total=1, connect=1, read=0, status=1, allowed_methods={"GET", "HEAD"}, status_forcelist={429, 502, 503, 504}, respect_retry_after_header=True, backoff_factor=0.5)` and cap Retry-After by the TaskContext deadline. Consume and close every response inside the adapter.

- [ ] **Step 3: Migrate direct callers without semantic changes**

Replace private Sessions and `requests.get/post` with `get_http_client()`. Preserve endpoint-specific timeouts by passing `(connect, read)` but also supply the overall TaskContext. AI Horde polling migration completes in Task 6.

- [ ] **Step 4: Add a static contract test**

Scan built-in source AST and fail on new direct `requests.get/post/Session` outside `utils/http_client.py` and explicit test fixtures.

- [ ] **Step 5: Run network-related tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_http_client.py tests\test_http_client_contract.py tests\test_network_failure_regression.py tests\test_flight_radar.py tests\test_gcd_comic_covers.py tests\test_steam_charts.py`

Expected: PASS.

Commit only caller files actually migrated:

```powershell
git add -- inkypi-weather/package/InkyPi/src/utils/http_client.py inkypi-weather/package/InkyPi/tests/test_http_client.py inkypi-weather/package/InkyPi/tests/test_http_client_contract.py
git add -p -- inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/src/utils/massive_market_data.py
git commit -m "fix: bound HTTP retries and response ownership"
```

### Task 4: Enforce safe image decode limits

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/utils/safe_image.py`
- Modify: `inkypi-weather/package/InkyPi/src/utils/image_loader.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_image_loader.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_safe_image.py`

**Interfaces:**
- Produces: `ImageLimits`, `safe_open_image()` returning a fully loaded detached `Image.Image`.
- Consumes: bytes, paths, file-like objects, and owned response streams.

- [ ] **Step 1: Write failing pixel, dimension, warning, and ownership tests**

```python
def test_safe_open_rejects_large_dimensions_before_load(monkeypatch):
    image = header_only_png(width=9000, height=10)
    called = False
    monkeypatch.setattr(Image.Image, "load", lambda self: pytest.fail("load called"))
    with pytest.raises(ImageLimitError, match="dimension"):
        safe_open_image(BytesIO(image))


def test_safe_open_returns_detached_first_frame(tmp_path):
    path = tmp_path / "animated.gif"
    write_two_frame_gif(path)
    result = safe_open_image(path)
    path.unlink()
    assert result.getpixel((0, 0)) == expected_first_frame_pixel()
```

- [ ] **Step 2: Run and implement**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_safe_image.py`

Expected: FAIL on missing module.

```python
@dataclass(frozen=True)
class ImageLimits:
    max_bytes: int = 25 * 1024 * 1024
    max_width: int = 8192
    max_height: int = 8192
    max_pixels: int = 8_000_000
    allowed_formats: frozenset[str] = frozenset({"JPEG", "PNG", "WEBP", "GIF"})


def safe_open_image(source, *, limits=ImageLimits(), first_frame=True):
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(source) as opened:
            width, height = opened.size
            if width > limits.max_width or height > limits.max_height:
                raise ImageLimitError("image dimension exceeds limit")
            if width * height > limits.max_pixels:
                raise ImageLimitError("image pixel count exceeds limit")
            if opened.format not in limits.allowed_formats:
                raise ImageLimitError("image format is not allowed")
            if first_frame:
                opened.seek(0)
            normalized = ImageOps.exif_transpose(opened)
            normalized.load()
            return normalized.copy()
```

- [ ] **Step 3: Delegate all AdaptiveImageLoader decode paths**

Keep low-memory streaming, but inspect header and apply limits before full load; use one `SpooledTemporaryFile` instead of chunk list plus join. Context-manage streamed responses.

- [ ] **Step 4: Migrate first high-risk decoders**

Migrate AI Image/Multiverse, GCD, Ticketmaster, Sports logo, FlightRadar, Dota/LoL/Steam profile, SpeciesRadar, NatGeo, and Wpotd network decodes. Newspaper PDF remains on its separate bounded PDF path.

- [ ] **Step 5: Run image/plugin tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_safe_image.py tests\test_image_loader.py tests\test_ticketmaster_events.py tests\test_sports_dashboard.py -k "image or logo or ticketmaster"`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/utils/safe_image.py inkypi-weather/package/InkyPi/src/utils/image_loader.py inkypi-weather/package/InkyPi/tests/test_safe_image.py inkypi-weather/package/InkyPi/tests/test_image_loader.py
git add -p -- inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/tests
git commit -m "fix: reject unsafe image decodes"
```

### Task 5: Consolidate Chromium into BrowserRenderer

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/utils/browser_renderer.py`
- Modify: `inkypi-weather/package/InkyPi/src/utils/image_utils.py:106-242`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py:120-137`
- Create: `inkypi-weather/package/InkyPi/tests/test_browser_renderer.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_image_utils.py`

**Interfaces:**
- Produces: `BrowserRenderer.render_html()`, `render_url()`, `close()`, `get_browser_renderer()`.
- Consumes: TaskContext; the operations plan later supplies `SSRFPolicy` and guarded remote callers.

- [ ] **Step 1: Write failing timeout cleanup and global-serialization tests**

```python
def test_timeout_kills_waits_and_removes_all_temp_paths(fake_process, tmp_path):
    renderer = BrowserRenderer(binary="chromium", temp_root=tmp_path, popen=fake_process.popen)
    assert renderer.render_html("<p>x</p>", viewport=(800, 480), context=expired_context()) is None
    assert fake_process.terminated
    assert fake_process.killed
    assert fake_process.waited
    assert list(tmp_path.iterdir()) == []


def test_two_renderer_calls_never_overlap(global_renderer, blocking_process):
    run_two_threads(global_renderer.render_html)
    assert blocking_process.maximum_concurrency == 1
```

- [ ] **Step 2: Run tests and implement BrowserRenderer**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_browser_renderer.py`

Expected: FAIL on missing module.

Implement separate local-HTML and remote-URL entrypoints, one global semaphore, per-job profile, no downloads, 1 MiB stdout/stderr caps, registered PID tracking, and terminate→wait→kill→wait cleanup. Negative cache key is normalized target + viewport + renderer version, TTL 600 seconds. `render_url()` requires a non-null validator callback and fails closed; no remote plugin caller migrates until the operations SSRF task installs the egress proxy.

- [ ] **Step 3: Keep image_utils functions as compatibility wrappers**

`take_screenshot_html()` and BasePlugin call `get_browser_renderer().render_html`; `take_screenshot()` rejects remote URLs without a validator and otherwise calls the appropriate renderer method. Remove `--no-sandbox`.

- [ ] **Step 4: Keep remote callers fail-closed until SSRF integration**

Add this contract to `test_browser_renderer.py`: monkeypatch `render_url()` to fail and prove existing remote plugin tests never reach it through an unvalidated compatibility wrapper. The operations SSRF task performs those four migrations atomically with the egress policy.

- [ ] **Step 5: Run browser-related tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_browser_renderer.py tests\test_image_utils.py -k "browser or chromium or unvalidated"`

Expected: PASS and 100 simulated failures leave zero PID/profile/temp growth.

```powershell
git add -- inkypi-weather/package/InkyPi/src/utils/browser_renderer.py inkypi-weather/package/InkyPi/src/utils/image_utils.py inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py inkypi-weather/package/InkyPi/tests/test_browser_renderer.py inkypi-weather/package/InkyPi/tests/test_image_utils.py
git commit -m "fix: centralize bounded Chromium rendering"
```

### Task 6: Isolate AI Horde and other minute-scale work

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime/long_task_executor.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/ai_image_multiverse/ai_image_multiverse.py:505-655`
- Create: `inkypi-weather/package/InkyPi/tests/test_long_task_executor.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_daily_ai_news.py`

**Interfaces:**
- Produces: `LongTaskExecutor.submit/cancel/shutdown()` and `LongTaskHandle`.
- Consumes: TaskContext, immutable instance identity, and HttpClient.

- [ ] **Step 1: Write failing capacity, cancellation, and stale-result tests**

```python
def test_executor_is_bounded_and_kills_uncooperative_process(executor):
    running = executor.submit("block", {}, context=context(deadline=0.05), instance_identity=identity())
    queued = executor.submit("block", {}, context=context(deadline=1), instance_identity=identity("two"))
    with pytest.raises(LongTaskQueueFull):
        executor.submit("block", {}, context=context(deadline=1), instance_identity=identity("three"))
    assert running.result(timeout=1).status == "abandoned"
    executor.shutdown(deadline_monotonic=time.monotonic() + 1)
    assert executor.active_processes == ()
```

- [ ] **Step 2: Run and implement process isolation**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_long_task_executor.py`

Expected: FAIL on missing module.

Use one worker process and two queued payloads. Serialize only primitive payloads and output image paths/bytes. Parent validates UUID/generation/revision before accepting results. Cancel sends an event, then terminate/kill/wait at deadline.

- [ ] **Step 3: Convert Horde polling to TaskContext**

Use `with HttpClient`-owned responses, deadline-aware waits (`cancel_event.wait(min(10, remaining))`), a default 180-second total deadline, and safe image decode. Remove the private Session and 120×10-second loop.

- [ ] **Step 4: Run AI and executor tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_long_task_executor.py tests\test_daily_ai_news.py -k "horde or long_task"`

Expected: PASS; cancellation leaves no process or late cache commit.

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime/long_task_executor.py inkypi-weather/package/InkyPi/src/plugins/ai_image_multiverse/ai_image_multiverse.py inkypi-weather/package/InkyPi/tests/test_long_task_executor.py inkypi-weather/package/InkyPi/tests/test_daily_ai_news.py
git commit -m "fix: isolate and cancel minute-scale plugin work"
```

### Task 7: Enforce managed memory and disk cache budgets

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/utils/cache_manager.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py:89-108`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/tech_pulse/tech_pulse.py`
- Modify: `inkypi-weather/package/InkyPi/src/plugins/ticketmaster_events/ticketmaster_events.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_cache_manager.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_ticketmaster_events.py`

**Interfaces:**
- Produces: `CacheBudget`, `CacheManager.namespace()`, `CacheNamespace`, `ImageLRUCache`.
- Consumes: atomic writes, RuntimePaths, HealthPublisher.

- [ ] **Step 1: Write failing budget and path-safety tests**

```python
def test_namespace_prunes_lru_before_write_and_stays_under_budget(cache_manager):
    namespace = cache_manager.namespace("ticketmaster", CacheBudget(3600, 2, 10))
    namespace.put_bytes("one", b"12345", suffix=".jpg")
    namespace.put_bytes("two", b"12345", suffix=".jpg")
    namespace.put_bytes("three", b"12345", suffix=".jpg")
    status = namespace.status()
    assert status.files <= 2
    assert status.bytes <= 10
    assert not namespace.path("one", ".jpg").exists()


def test_namespace_rejects_symlink_escape(cache_manager, tmp_path):
    namespace = cache_manager.namespace("sports", CacheBudget(3600, 10, 1000))
    make_escape_symlink(namespace.root / "escape", tmp_path.parent)
    with pytest.raises(CachePathError):
        namespace.path("escape/secret", ".png")
```

- [ ] **Step 2: Run and implement CacheManager**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_cache_manager.py`

Expected: FAIL on missing module.

`CacheNamespace` resolves every path and verifies it remains under its managed root, refuses symlinks, prunes by last access/mtime before writes, rejects an individually oversize object, and removes managed `.tmp` older than one hour at startup/daily maintenance.

- [ ] **Step 3: Implement byte-bounded ImageLRUCache**

Estimate bytes as `width * height * bands`; evict until both `max_entries` and `max_bytes` hold. Provide `clear()` for memory-pressure maintenance.

- [ ] **Step 4: Migrate high-growth caches**

Sports `TEAM_LOGO_CACHE` and `FLAG_IMAGE_CACHE` become ImageLRUCache. Sports disk logos, TechPulse previews, and Ticketmaster event images use CacheNamespace. Existing user/ignored directories outside managed roots are never deleted.

- [ ] **Step 5: Run cache/plugin tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_cache_manager.py tests\test_sports_dashboard.py tests\test_tech_pulse.py tests\test_ticketmaster_events.py`

Expected: PASS, including 1,000-key tests without linear cache growth.

```powershell
git add -- inkypi-weather/package/InkyPi/src/utils/cache_manager.py inkypi-weather/package/InkyPi/src/plugins/base_plugin/base_plugin.py inkypi-weather/package/InkyPi/tests/test_cache_manager.py
git add -p -- inkypi-weather/package/InkyPi/src/plugins/sports_dashboard/common.py inkypi-weather/package/InkyPi/src/plugins/tech_pulse/tech_pulse.py inkypi-weather/package/InkyPi/src/plugins/ticketmaster_events/ticketmaster_events.py inkypi-weather/package/InkyPi/tests/test_sports_dashboard.py inkypi-weather/package/InkyPi/tests/test_tech_pulse.py inkypi-weather/package/InkyPi/tests/test_ticketmaster_events.py
git commit -m "fix: enforce plugin cache budgets"
```

### Task 8: Plugin resource regression gate

**Files:**
- Create: `inkypi-weather/package/InkyPi/tests/test_plugin_resource_contract.py`

**Interfaces:**
- Consumes: Tasks 1-7.
- Produces: a permanent guard against reintroducing direct requests, unsafe network image decode, private Chromium, or unbounded module caches.

- [ ] **Step 1: Add the static resource contract**

AST-scan built-ins and fail on:

- direct `requests.get/post/Session` outside the approved HTTP module;
- `Image.open(BytesIO(response.content))` or equivalent network decode;
- `subprocess.Popen` with Chromium names outside BrowserRenderer;
- module-level mutable dicts with names ending `_IMAGE_CACHE` or `_LOGO_CACHE` outside CacheManager wrappers;
- manifest schema other than v2 for built-ins.

- [ ] **Step 2: Run the plugin-focused suite**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_plugin_resource_contract.py tests\test_plugin_manifest.py tests\test_plugin_settings.py tests\test_http_client.py tests\test_http_client_contract.py tests\test_safe_image.py tests\test_image_loader.py tests\test_browser_renderer.py tests\test_long_task_executor.py tests\test_cache_manager.py tests\test_refresh_task.py tests\test_tech_pulse.py tests\test_ticketmaster_events.py tests\test_sports_dashboard.py`

Expected: PASS.

- [ ] **Step 3: Commit the permanent guard**

```powershell
git add -- inkypi-weather/package/InkyPi/tests/test_plugin_resource_contract.py
git commit -m "test: prevent unbounded plugin resource paths"
```
