# State and Display Integrity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make configuration and display publication durable, recoverable, and truthful, then expose non-blocking health/readiness state.

**Architecture:** Introduce one strict atomic-file primitive, a versioned ConfigStore with last-known-good rotation, a separate RuntimeStateStore, and a display transaction whose single authority is an atomic manifest pointing to immutable images. Hardware, startup, and health use the runtime lifecycle and TaskContext produced by the scheduler plan.

**Tech Stack:** Python 3.11, pathlib, dataclasses, JSON, Pillow, Flask, pytest, Linux fsync/systemd semantics.

## Global Constraints

- This plan starts only after `2026-07-09-runtime-scheduler-hardening.md` passes.
- Operations plan Task 1 (`RuntimePaths`) completes before ConfigStore and display paths are wired.
- User configuration, runtime state, and display commits have separate files and owners.
- A candidate configuration becomes visible in memory only after validation and durable persistence.
- The authoritative display state is one atomic manifest; `current_image.png` is compatibility output only.
- Health endpoints perform no external I/O and return from an immutable snapshot within 50 ms locally, with a 200 ms test ceiling.
- Display deadlines are model-specific; an unconfigured Waveshare model defaults to 90 seconds.
- Existing configuration remains readable and previous releases can ignore new sidecar files.

---

### Task 1: Add strict atomic file primitives

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/utils/atomic_file.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_atomic_file.py`

**Interfaces:**
- Produces: `atomic_write_bytes()`, `atomic_write_json()`, `atomic_write_image()`, `fsync_directory()`.
- Consumes: standard library only.

- [ ] **Step 1: Write failing atomicity, permission, and cleanup tests**

```python
def test_atomic_write_json_fsyncs_file_and_parent(monkeypatch, tmp_path):
    fsynced = []
    monkeypatch.setattr(os, "fsync", lambda fd: fsynced.append(fd))
    target = tmp_path / "device.json"
    atomic_write_json(target, {"version": 1}, mode=0o600)
    assert json.loads(target.read_text(encoding="utf-8")) == {"version": 1}
    assert stat.S_IMODE(target.stat().st_mode) == 0o600
    assert len(fsynced) >= 2


def test_atomic_write_failure_keeps_old_file_and_removes_temp(monkeypatch, tmp_path):
    target = tmp_path / "device.json"
    target.write_text('{"old": true}\n', encoding="utf-8")
    monkeypatch.setattr(os, "replace", Mock(side_effect=OSError("disk")))
    with pytest.raises(OSError, match="disk"):
        atomic_write_json(target, {"new": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"old": True}
    assert list(tmp_path.glob(".*.tmp")) == []
```

- [ ] **Step 2: Run the tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_atomic_file.py`

Expected: FAIL because the module is absent.

- [ ] **Step 3: Implement strict same-directory writes without direct-write fallback**

```python
def atomic_write_bytes(path, payload, *, mode=None, fsync_directory=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary)
    try:
        if mode is not None:
            os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if fsync_directory:
            fsync_parent(path.parent)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_write_json(path, payload, *, mode=None, fsync_directory=True):
    encoded = (json.dumps(payload, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    atomic_write_bytes(path, encoded, mode=mode, fsync_directory=fsync_directory)


def atomic_write_image(path, image, *, image_format="PNG", mode=0o600):
    output = BytesIO()
    image.save(output, format=image_format)
    atomic_write_bytes(path, output.getvalue(), mode=mode)
```

On Windows, `fsync_parent()` is a documented no-op after the atomic replace; Linux opens the directory with `O_DIRECTORY` and fsyncs it.

- [ ] **Step 4: Run the tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_atomic_file.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/utils/atomic_file.py inkypi-weather/package/InkyPi/tests/test_atomic_file.py
git commit -m "feat: add durable atomic file writes"
```

### Task 2: Implement ConfigStore transactions and LKG recovery

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/config_store.py`
- Modify: `inkypi-weather/package/InkyPi/src/config.py:24-182`
- Create: `inkypi-weather/package/InkyPi/tests/test_config_store.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_config_env_key_aliases.py`

**Interfaces:**
- Consumes: Task 1 atomic writes and runtime plan Playlist snapshots.
- Produces: `ConfigSnapshot`, `ConfigStatus`, `ConfigStore.load()`, `snapshot()`, `commit()`, and a compatible `Config` facade.

- [ ] **Step 1: Write failing CAS, disk-failure, and LKG tests**

```python
def test_failed_persist_does_not_publish_candidate(monkeypatch, config_store):
    before = config_store.snapshot()
    monkeypatch.setattr("src.config_store.atomic_write_json", Mock(side_effect=OSError("full")))
    with pytest.raises(ConfigPersistError):
        config_store.commit(expected_version=before.version, candidate={"name": "new"})
    assert config_store.snapshot() == before


def test_corrupt_primary_recovers_valid_lkg_and_quarantines_source(tmp_path):
    primary = tmp_path / "device.json"
    lkg = tmp_path / "device.lkg.1.json"
    primary.write_text("{bad", encoding="utf-8")
    lkg.write_text('{"schema_version": 1, "display_type": "mock"}', encoding="utf-8")
    result = ConfigStore(primary).load()
    assert result.source == "lkg"
    assert result.snapshot.data["display_type"] == "mock"
    assert list(tmp_path.glob("device.corrupt.*.json"))
```

- [ ] **Step 2: Run focused tests and verify old Config fails**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_config_store.py tests\test_config_env_key_aliases.py`

Expected: FAIL because ConfigStore is absent and corrupt JSON currently becomes `{}`.

- [ ] **Step 3: Implement versioned candidate persistence**

```python
@dataclass(frozen=True)
class ConfigSnapshot:
    version: int
    data: Mapping[str, Any]


@dataclass(frozen=True)
class ConfigStatus:
    valid: bool
    source: str
    version: int
    degraded_reason: str | None = None


class ConfigStore:
    def __init__(self, path, *, validator=validate_device_config):
        self.path = Path(path)
        self.lkg_paths = [self.path.with_name("device.lkg.1.json"),
                          self.path.with_name("device.lkg.2.json")]
        self._lock = threading.RLock()
        self.validator = validator
        self._snapshot = ConfigSnapshot(0, MappingProxyType({}))
        self._status = ConfigStatus(False, "missing", 0, "not_loaded")

    def commit(self, *, expected_version, candidate):
        validated = self.validator(deepcopy(dict(candidate)))
        with self._lock:
            if expected_version != self._snapshot.version:
                raise ConfigConflictError(expected_version, self._snapshot.version)
            next_snapshot = ConfigSnapshot(expected_version + 1, freeze_mapping(validated))
            atomic_write_json(self.path, dict(next_snapshot.data), mode=0o600)
            self._rotate_lkg(dict(next_snapshot.data))
            self._snapshot = next_snapshot
            self._status = ConfigStatus(True, "primary", next_snapshot.version)
            return next_snapshot
```

Validation checks an object root, required resolution/display fields when present, playlist structure, and positive refresh intervals. Rotate at most two valid LKG files; quarantine corrupt originals before restoring.

- [ ] **Step 4: Convert Config into a compatibility facade**

`Config.get_config()` reads the current immutable snapshot. `update_value()` and `update_config()` build a candidate dictionary and call CAS commit. `write_config()` obtains one locked PlaylistManager snapshot, releases the manager lock, builds the candidate, and commits; it never falls back to direct overwrite.

- [ ] **Step 5: Run Config and model suites**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_config_store.py tests\test_config_env_key_aliases.py tests\test_model.py`

Expected: PASS.

- [ ] **Step 6: Commit ConfigStore**

```powershell
git add -- inkypi-weather/package/InkyPi/src/config_store.py inkypi-weather/package/InkyPi/src/config.py inkypi-weather/package/InkyPi/tests/test_config_store.py inkypi-weather/package/InkyPi/tests/test_config_env_key_aliases.py
git commit -m "feat: make device configuration transactional"
```

### Task 3: Separate high-frequency runtime state

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime/runtime_state.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_runtime_state_store.py`
- Modify: `inkypi-weather/package/InkyPi/src/refresh_task.py`

**Interfaces:**
- Produces: `RuntimeStateStore.record_attempt/success/failure()`, `set_display_state()`, `snapshot()`, `prune()`.
- Consumes: stable instance UUIDs and atomic writes.

- [ ] **Step 1: Write failing attempt/success separation tests**

```python
def test_failure_does_not_advance_success_time(tmp_path):
    store = RuntimeStateStore(tmp_path / "runtime.json")
    store.record_success("one", "2026-07-09T10:00:00+00:00")
    store.record_failure("one", "2026-07-09T10:01:00+00:00", "offline",
                         "2026-07-09T10:01:30+00:00")
    state = store.snapshot().instances["one"]
    assert state.last_success_at == "2026-07-09T10:00:00+00:00"
    assert state.last_failure_at == "2026-07-09T10:01:00+00:00"
```

- [ ] **Step 2: Run the test and implement the store**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_state_store.py`

Expected: FAIL on missing module.

Implement a lock-protected in-memory snapshot, debounce disk persistence to at most once per five seconds, cap instance records to current UUIDs plus 64 recent tombstones, and flush synchronously during lifecycle drain.

- [ ] **Step 3: Route RefreshTask attempt/failure/success updates to the store**

Remove background failure writes to `latest_refresh_time`. Preserve legacy success reads during migration, but only `record_success()` marks cache fresh.

- [ ] **Step 4: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_state_store.py tests\test_refresh_task.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime/runtime_state.py inkypi-weather/package/InkyPi/src/refresh_task.py inkypi-weather/package/InkyPi/tests/test_runtime_state_store.py inkypi-weather/package/InkyPi/tests/test_refresh_task.py
git commit -m "fix: separate refresh attempts from successful state"
```

### Task 4: Add manifest-backed display transactions

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/display/display_transaction.py`
- Modify: `inkypi-weather/package/InkyPi/src/display/display_manager.py:21-84`
- Create: `inkypi-weather/package/InkyPi/tests/test_display_transaction.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_display_manager.py`

**Interfaces:**
- Consumes: `TaskContext`, atomic writes, and RuntimeStateStore.
- Produces: `PreparedDisplay`, `DisplayCommit`, `DisplayTransaction.prepare/commit/current/recover()`.

- [ ] **Step 1: Write failing hardware failure, metadata-only, and unknown-state tests**

```python
def test_hardware_failure_keeps_previous_manifest(display_transaction, fake_driver):
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 1)
    first = display_transaction.commit(
        display_transaction.prepare(red_image(), logical_target={"id": "one"}),
        task_context=context,
    )
    fake_driver.error = RuntimeError("busy")
    with pytest.raises(RuntimeError, match="busy"):
        display_transaction.commit(
            display_transaction.prepare(blue_image(), logical_target={"id": "two"}),
            task_context=context,
        )
    assert display_transaction.current().commit_id == first.commit_id


def test_same_pixels_new_logical_target_creates_metadata_only_commit(display_transaction, fake_driver):
    context = TaskContext.never_cancelled(deadline_monotonic=time.monotonic() + 1)
    first = display_transaction.commit(
        display_transaction.prepare(red_image(), logical_target={"id": "one"}),
        task_context=context,
    )
    second = display_transaction.commit(
        display_transaction.prepare(red_image(), logical_target={"id": "two"}),
        task_context=context,
    )
    assert fake_driver.calls == 1
    assert second.commit_id != first.commit_id
    assert second.logical_target == {"id": "two"}
```

- [ ] **Step 2: Run display tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_display_transaction.py tests\test_display_manager.py`

Expected: FAIL because current code writes `current_image.png` before hardware.

- [ ] **Step 3: Implement prepare and manifest commit**

```python
@dataclass(frozen=True)
class PreparedDisplay:
    commit_id: str
    image_path: Path
    pixel_hash: str
    hardware_fingerprint: str
    logical_target: Mapping[str, str]
    instance_revision: tuple[int, int] | None


class DisplayTransaction:
    def prepare(self, image, *, image_settings=(), logical_target=None,
                instance_revision=None):
        final = self.manager.prepare_image(image, image_settings=image_settings)
        commit_id = uuid4().hex
        object_path = self.objects_dir / f"{commit_id}.png"
        atomic_write_image(object_path, final)
        return PreparedDisplay(commit_id, object_path, compute_image_hash(final),
                               self.manager.hardware_fingerprint(image_settings),
                               freeze_mapping(logical_target or {}), instance_revision)

    def commit(self, prepared, *, task_context):
        previous = self.current()
        hardware_needed = previous is None or (
            previous.pixel_hash != prepared.pixel_hash
            or previous.hardware_fingerprint != prepared.hardware_fingerprint
        )
        if hardware_needed:
            self.manager.write_hardware_path(prepared.image_path, task_context=task_context)
        manifest = self._manifest_from_prepared(prepared, hardware_needed)
        try:
            atomic_write_json(self.manifest_path, manifest, mode=0o600)
        except OSError:
            self.runtime_state.set_display_state("display_unknown")
            raise DisplayCommitUnknownError(prepared.commit_id)
        self.runtime_state.set_display_state("committed", prepared.commit_id)
        self._publish_compatibility_image(prepared.image_path)
        return DisplayCommit.from_dict(manifest)
```

- [ ] **Step 4: Split DisplayManager preparation from hardware I/O**

Use `image_settings=()` instead of a mutable list default. `prepare_image()` performs orientation, resize, inversion, and enhancement. `write_hardware()` is the only driver call and never writes current image first.

- [ ] **Step 5: Implement recovery**

If state is unknown or orphan objects are newer than the manifest, re-submit the last committed image to hardware under a TaskContext, then mark committed. If no valid manifest exists, remain not-ready until a normal display succeeds.

- [ ] **Step 6: Run display tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_display_transaction.py tests\test_display_manager.py tests\test_refresh_task.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/display/display_transaction.py inkypi-weather/package/InkyPi/src/display/display_manager.py inkypi-weather/package/InkyPi/tests/test_display_transaction.py inkypi-weather/package/InkyPi/tests/test_display_manager.py inkypi-weather/package/InkyPi/src/refresh_task.py
git commit -m "feat: publish display state through atomic manifests"
```

### Task 5: Serve the authoritative display manifest

**Files:**
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/main.py:12-42`
- Modify: `inkypi-weather/package/InkyPi/src/templates/inky.html`
- Create: `inkypi-weather/package/InkyPi/tests/test_main_blueprint.py`

**Interfaces:**
- Consumes: `DisplayTransaction.current_image_path()` and current commit time.
- Produces: correct conditional GET without timezone arithmetic.

- [ ] **Step 1: Write failing conditional request tests**

```python
def test_current_image_uses_manifest_path_and_standard_conditional_get(client, display_transaction):
    commit = commit_test_image(display_transaction)
    first = client.get("/api/current_image")
    assert first.status_code == 200
    assert first.headers["ETag"] == f'"{commit.commit_id}"'
    second = client.get("/api/current_image", headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 304
```

- [ ] **Step 2: Run the test and implement the route**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_main_blueprint.py`

Expected: FAIL because the route hard-codes the compatibility PNG.

Use `send_file(path, conditional=True, etag=commit.commit_id, last_modified=commit.committed_at)` and `Cache-Control: no-cache`; the template always references `/api/current_image`.

- [ ] **Step 3: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_main_blueprint.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/blueprints/main.py inkypi-weather/package/InkyPi/src/templates/inky.html inkypi-weather/package/InkyPi/tests/test_main_blueprint.py
git commit -m "fix: serve only committed display images"
```

### Task 6: Bound Waveshare BUSY waits and degrade startup safely

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/display/busy_wait.py`
- Modify: `inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in5_V2.py:85-93`
- Modify: `inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in3e.py`
- Modify: `inkypi-weather/package/InkyPi/src/display/waveshare_display.py`
- Modify: `inkypi-weather/package/InkyPi/src/utils/app_utils.py:104-108`
- Modify: `inkypi-weather/package/InkyPi/src/inkypi.py:103-140`
- Create: `inkypi-weather/package/InkyPi/tests/test_waveshare_busy.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_inkypi_startup.py`

**Interfaces:**
- Consumes: `TaskContext` and lifecycle drain.
- Produces: `wait_while_busy()` and `display_startup_image_best_effort()`.

- [ ] **Step 1: Write failing stuck-BUSY and offline-startup tests**

```python
def test_busy_wait_times_out_without_spinning():
    clock = FakeClock()
    with pytest.raises(DisplayBusyTimeout):
        wait_while_busy(lambda: 0, timeout_seconds=0.05,
                        clock=clock.monotonic, sleeper=clock.sleep,
                        task_context=TaskContext.never_cancelled(deadline_monotonic=1.0),
                        stage="epd7in5.init")
    assert clock.sleep_calls > 0


def test_offline_startup_still_runs_web_server(monkeypatch):
    monkeypatch.setattr("src.utils.app_utils.socket.socket", OfflineSocket)
    app = build_application(dev_mode=True)
    assert app.config["STARTUP_DEGRADED"] is True
    assert app.config["REFRESH_TASK"] is not None
```

- [ ] **Step 2: Run tests and implement busy wait**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_waveshare_busy.py tests\test_inkypi_startup.py`

Expected: FAIL because epd7in5 spins forever and app construction is global.

```python
def wait_while_busy(read_busy, *, task_context, stage, timeout_seconds=90,
                    poll_interval_seconds=0.01, clock=time.monotonic,
                    sleeper=time.sleep):
    deadline = min(task_context.deadline_monotonic, clock() + timeout_seconds)
    while read_busy() == 0:
        task_context.raise_if_cancelled()
        if clock() >= deadline:
            raise DisplayBusyTimeout(stage, timeout_seconds)
        sleeper(poll_interval_seconds)
```

- [ ] **Step 3: Move application startup into testable functions**

Create `build_application(dev_mode=False)` for dependency construction and `run()` for Wi-Fi/startup image/server lifecycle. `get_ip_address(default="Unknown")` catches `OSError`; startup image/display failures set degraded health and never prevent Waitress from starting. The top-level module only parses args and calls `run()`.

- [ ] **Step 4: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_waveshare_busy.py tests\test_inkypi_startup.py tests\test_network_utils.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/display/busy_wait.py inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in5_V2.py inkypi-weather/package/InkyPi/src/display/waveshare_epd/epd7in3e.py inkypi-weather/package/InkyPi/src/display/waveshare_display.py inkypi-weather/package/InkyPi/src/utils/app_utils.py inkypi-weather/package/InkyPi/src/inkypi.py inkypi-weather/package/InkyPi/tests/test_waveshare_busy.py inkypi-weather/package/InkyPi/tests/test_inkypi_startup.py
git commit -m "fix: bound hardware waits and tolerate offline startup"
```

### Task 7: Publish non-blocking health and readiness

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/health.py`
- Create: `inkypi-weather/package/InkyPi/src/blueprints/health.py`
- Modify: `inkypi-weather/package/InkyPi/src/inkypi.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_health_snapshot.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_health_blueprint.py`

**Interfaces:**
- Consumes: lifecycle, queue, ConfigStore, RuntimeStateStore, DisplayTransaction, and later CacheManager snapshots.
- Produces: `HealthPublisher`, `ReadinessEvaluator`, `/healthz`, `/readyz`.

- [ ] **Step 1: Write failing state and response-budget tests**

```python
def test_readyz_does_not_wait_for_core_locks(client, locked_runtime_components):
    started = time.monotonic()
    response = client.get("/readyz")
    elapsed = time.monotonic() - started
    assert elapsed < 0.2
    assert response.status_code in {200, 503}


def test_queue_full_is_degraded_until_sustained_with_stalled_heartbeat():
    snapshot = healthy_snapshot(queue_full_since=100.0, scheduler_heartbeat=159.0)
    assert ReadinessEvaluator().evaluate(snapshot, now_monotonic=160.0).status == "degraded"
    snapshot = replace(snapshot, scheduler_heartbeat=90.0)
    assert ReadinessEvaluator().evaluate(snapshot, now_monotonic=161.0).status == "not_ready"
```

- [ ] **Step 2: Run health tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_health_snapshot.py tests\test_health_blueprint.py`

Expected: FAIL on missing modules.

- [ ] **Step 3: Implement immutable snapshot publication**

`HealthPublisher.publish_component(name, value)` takes only its own short lock, constructs a new frozen `HealthSnapshot`, and replaces one reference. `snapshot()` performs an unlocked reference read. The evaluator returns `starting/not_ready` as 503 and `ready/degraded` as 200 using the exact thresholds from the design.

- [ ] **Step 4: Register public summary and authenticated detail routes**

Public bodies include only status, release ID, boot ID, and uptime. When the security plan installs authentication, an authenticated request also receives component details; URLs omit query strings and settings expose key names only.

- [ ] **Step 5: Run state/display regression gate and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_atomic_file.py tests\test_config_store.py tests\test_runtime_state_store.py tests\test_display_transaction.py tests\test_display_manager.py tests\test_main_blueprint.py tests\test_waveshare_busy.py tests\test_inkypi_startup.py tests\test_health_snapshot.py tests\test_health_blueprint.py tests\test_refresh_task.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/health.py inkypi-weather/package/InkyPi/src/blueprints/health.py inkypi-weather/package/InkyPi/src/inkypi.py inkypi-weather/package/InkyPi/tests/test_health_snapshot.py inkypi-weather/package/InkyPi/tests/test_health_blueprint.py
git commit -m "feat: expose non-blocking health and readiness"
```
