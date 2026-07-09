# Operations Security and Release Safety Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give InkyPi stable production paths, authenticated administration, bounded requests, safe browser egress, least-privilege service operation, transactional upgrades, and reproducible release gates.

**Architecture:** Centralize runtime paths and secret metadata first. Install application-wide request guards before individual routes, isolate root-only actions in a fixed-command Unix-socket broker, and ship releases into immutable version directories controlled by a durable update journal. Health/readiness from the state plan is the commit/rollback oracle.

**Tech Stack:** Python 3.11, Flask/Werkzeug, JSON, Unix sockets, systemd, Bash, PowerShell, GitHub Actions, pytest.

## Global Constraints

- Production code is immutable under `/opt/inkypi/releases/<release-id>` with `current` and `previous` symlinks.
- Secrets live in `/etc/inkypi/inkypi.env`; persistent data lives in `/var/lib/inkypi`; managed cache lives in `/var/cache/inkypi`.
- All mutating requests require authentication, Host validation, same-origin/CSRF validation, and bounded rate limiting.
- High-risk operations are never anonymous on legacy installs; upgrade generates a root-readable one-time pairing token.
- Default request body is 8 MiB, individual upload is 5 MiB, and multipart parts are capped at 128.
- Screenshot/browser remote targets fail closed on loopback, private, link-local, metadata, rebinding, and unsafe redirect targets.
- Main Web/plugin service runs as `inkypi`; only a fixed-command broker runs as root.
- New release readiness must return the exact target release ID within a 120-second grace period or the updater rolls back.
- No deploy, systemd install, user creation, or data purge occurs without explicit live-device authorization.

---

### Task 1: Centralize runtime paths

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/runtime_paths.py`
- Modify: `inkypi-weather/package/InkyPi/src/config.py:24-35`
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/apikeys.py:13-16`
- Modify: `inkypi-weather/package/InkyPi/src/inkypi.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_runtime_paths.py`

**Interfaces:**
- Produces: immutable `RuntimePaths.from_environment(dev_mode)`.
- Consumes: release ID and optional `INKYPI_*` environment overrides.

- [ ] **Step 1: Write failing dev/production path tests**

```python
def test_production_paths_are_outside_release_tree(monkeypatch):
    monkeypatch.setenv("INKYPI_RELEASE_ID", "abc123")
    paths = RuntimePaths.from_environment(dev_mode=False)
    assert paths.release_id == "abc123"
    assert paths.config_file == Path("/var/lib/inkypi/config/device.json")
    assert paths.env_file == Path("/etc/inkypi/inkypi.env")
    assert paths.cache_dir == Path("/var/cache/inkypi")
    assert "/opt/inkypi/current" not in str(paths.config_file)


def test_dev_paths_remain_inside_checkout(tmp_path, monkeypatch):
    monkeypatch.setenv("INKYPI_DEV_ROOT", str(tmp_path))
    paths = RuntimePaths.from_environment(dev_mode=True)
    assert paths.config_file == tmp_path / "config" / "device_dev.json"
```

- [ ] **Step 2: Run and implement RuntimePaths**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_paths.py`

Expected: FAIL on missing module.

```python
@dataclass(frozen=True)
class RuntimePaths:
    release_id: str
    config_dir: Path
    data_dir: Path
    cache_dir: Path
    env_file: Path
    display_dir: Path
    plugin_image_dir: Path
    flask_secret_file: Path

    @property
    def config_file(self):
        return self.config_dir / "device.json"

    @classmethod
    def from_environment(cls, dev_mode=False):
        if dev_mode:
            root = Path(os.environ.get("INKYPI_DEV_ROOT", Path(__file__).resolve().parent))
            return cls(os.environ.get("INKYPI_RELEASE_ID", "development"),
                       root / "config", root / "data", root / ".cache",
                       root.parent / ".env", root / "static" / "display",
                       root / "static" / "images" / "plugins",
                       root / "config" / ".flask_secret")
        return cls(os.environ.get("INKYPI_RELEASE_ID", "unknown"),
                   Path("/var/lib/inkypi/config"), Path("/var/lib/inkypi/data"),
                   Path("/var/cache/inkypi"), Path("/etc/inkypi/inkypi.env"),
                   Path("/var/lib/inkypi/display"), Path("/var/lib/inkypi/plugins"),
                   Path("/var/lib/inkypi/config/flask_secret"))
```

- [ ] **Step 3: Inject paths into Config, API key storage, display/cache, and app construction**

Construct one RuntimePaths in `build_application()` and pass it to services. Preserve class attributes only as development compatibility aliases; production writes never target source directories.

- [ ] **Step 4: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_runtime_paths.py tests\test_config_env_key_aliases.py tests\test_apikeys.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/runtime_paths.py inkypi-weather/package/InkyPi/src/config.py inkypi-weather/package/InkyPi/src/blueprints/apikeys.py inkypi-weather/package/InkyPi/src/inkypi.py inkypi-weather/package/InkyPi/tests/test_runtime_paths.py
git commit -m "feat: separate runtime data from release code"
```

### Task 2: Make SecretSchema the single key registry

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/config/secret_schema.json`
- Create: `inkypi-weather/package/InkyPi/src/secret_schema.py`
- Modify: `inkypi-weather/package/InkyPi/src/config.py:11-22,184-212`
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/apikeys.py`
- Modify: `inkypi-weather/package/InkyPi/install/configure_api_keys.py`
- Generate: `inkypi-weather/package/InkyPi/install/api_key_registry.json`
- Generate: `inkypi-weather/package/InkyPi/.env.example`
- Create: `inkypi-weather/package/InkyPi/tests/test_secret_schema.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_secret_schema_plugin_contract.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_config_env_key_aliases.py`

**Interfaces:**
- Produces: `SecretEntry`, `SecretSchema.load/validate/resolve_names/public_registry()`.
- Consumes: standard library only so installer can load it before venv setup.

- [ ] **Step 1: Write failing schema drift and alias tests**

```python
def test_generated_registry_and_example_match_schema():
    schema = SecretSchema.load(DEFAULT_SCHEMA_PATH)
    assert json.loads(REGISTRY.read_text(encoding="utf-8")) == schema.registry_document()
    assert EXAMPLE.read_text(encoding="utf-8") == schema.env_example()


@pytest.mark.parametrize("canonical, alias", [
    ("OPENAI_API_KEY", "OPEN_AI_SECRET"),
    ("TICKETMASTER_API_KEY", "TICKETMASTER_CONSUMER_KEY"),
    ("TELEGRAM_BOT_TOKEN", "TG_BOT_TOKEN"),
    ("BAMBU_ACCESS_CODE", "BAMBU_LAB_ACCESS_CODE"),
])
def test_config_resolves_declared_alias(monkeypatch, config, canonical, alias):
    monkeypatch.setenv(alias, "value")
    assert config.load_env_key(canonical) == "value"
```

- [ ] **Step 2: Run tests and implement schema validation**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_secret_schema.py tests\test_config_env_key_aliases.py`

Expected: FAIL on missing schema and known registry drift.

The schema has one entry per canonical key with aliases, feature, label, secret/path type, and optional help URL. Validation rejects duplicate canonical names, alias collisions, and invalid env identifiers.

- [ ] **Step 3: Generate existing artifacts from the schema**

`configure_api_keys.py --generate-artifacts` writes registry and `.env.example` deterministically. The Web API key page calls `schema.public_registry()`; Config removes hard-coded `ENV_KEY_ALIASES`.

- [ ] **Step 4: Add plugin secret contract scanning**

AST-scan literal environment names and `load_env_key()` calls. Every secret used by Ticketmaster, Telegram, Bambu, OpenAI, Blizzard, Groq, Pixiv, and other built-ins must resolve to a schema entry.

- [ ] **Step 5: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_secret_schema.py tests\test_secret_schema_plugin_contract.py tests\test_config_env_key_aliases.py tests\test_apikeys.py`

Expected: PASS and artifact generation produces no diff on a second run.

```powershell
git add -- inkypi-weather/package/InkyPi/src/config/secret_schema.json inkypi-weather/package/InkyPi/src/secret_schema.py inkypi-weather/package/InkyPi/src/config.py inkypi-weather/package/InkyPi/src/blueprints/apikeys.py inkypi-weather/package/InkyPi/install/configure_api_keys.py inkypi-weather/package/InkyPi/install/api_key_registry.json inkypi-weather/package/InkyPi/.env.example inkypi-weather/package/InkyPi/tests/test_secret_schema.py inkypi-weather/package/InkyPi/tests/test_secret_schema_plugin_contract.py inkypi-weather/package/InkyPi/tests/test_config_env_key_aliases.py
git commit -m "feat: unify plugin secret metadata"
```

### Task 3: Bound request bodies and uploads

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/security/__init__.py`
- Create: `inkypi-weather/package/InkyPi/src/security/request_limits.py`
- Modify: `inkypi-weather/package/InkyPi/src/utils/app_utils.py:231-273`
- Modify: `inkypi-weather/package/InkyPi/src/inkypi.py:86-88`
- Create: `inkypi-weather/package/InkyPi/tests/test_request_limits.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py`

**Interfaces:**
- Produces: `UploadPolicy`, `configure_request_limits()`, `copy_limited_upload()`.
- Consumes: RuntimePaths temporary/data directories.

- [ ] **Step 1: Write failing 413 and cleanup tests**

```python
def test_request_larger_than_eight_mib_returns_json_413(client):
    response = client.post("/update_now", data=b"x" * (8 * 1024 * 1024 + 1),
                           content_type="application/octet-stream")
    assert response.status_code == 413
    assert response.get_json()["error_code"] == "request_too_large"


def test_partial_oversize_upload_is_removed(tmp_path):
    upload = FakeUpload(b"x" * (5 * 1024 * 1024 + 1))
    with pytest.raises(UploadTooLarge):
        copy_limited_upload(upload, tmp_path / "image.png", UploadPolicy())
    assert list(tmp_path.iterdir()) == []
```

- [ ] **Step 2: Run and implement limits**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_request_limits.py`

Expected: FAIL because `MAX_FORM_PARTS` is 10,000 and there is no body cap.

Configure `MAX_CONTENT_LENGTH=8*1024*1024`, `MAX_FORM_PARTS=128`, and an appropriate form-memory ceiling. Stream uploads to a same-directory temp file in 64 KiB chunks, count bytes independently of Content-Length, validate extension/content, fsync, and atomically replace.

- [ ] **Step 3: Route all three upload entrypoints through the policy**

Update playlist add, plugin update, and update-now via `handle_request_files()`; plugin policies may lower but never raise the global caps.

- [ ] **Step 4: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_request_limits.py tests\test_plugin_blueprint.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/security/__init__.py inkypi-weather/package/InkyPi/src/security/request_limits.py inkypi-weather/package/InkyPi/src/utils/app_utils.py inkypi-weather/package/InkyPi/src/inkypi.py inkypi-weather/package/InkyPi/tests/test_request_limits.py inkypi-weather/package/InkyPi/tests/test_plugin_blueprint.py
git commit -m "fix: bound request and upload memory"
```

### Task 4: Require authenticated, CSRF-safe administration

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/security/credentials.py`
- Create: `inkypi-weather/package/InkyPi/src/security/request_guard.py`
- Create: `inkypi-weather/package/InkyPi/src/security/rate_limit.py`
- Create: `inkypi-weather/package/InkyPi/src/blueprints/auth.py`
- Create: `inkypi-weather/package/InkyPi/src/templates/login.html`
- Create: `inkypi-weather/package/InkyPi/src/templates/setup_admin.html`
- Create: `inkypi-weather/package/InkyPi/src/static/inkypi-security.js`
- Modify: `inkypi-weather/package/InkyPi/src/inkypi.py`
- Modify: all base HTML templates to load `inkypi-security.js`
- Create: `inkypi-weather/package/InkyPi/install/bootstrap_admin.py`
- Modify: `inkypi-weather/package/InkyPi/install/inkypi`
- Create: `inkypi-weather/package/InkyPi/tests/test_credentials.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_auth_blueprint.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_request_guard.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_write_route_security.py`

**Interfaces:**
- Produces: `CredentialStore`, `BoundedRateLimiter`, `install_request_guards()`, auth/setup routes, `current_admin_authenticated()`.
- Consumes: RuntimePaths, stable Flask secret, and Health public/detail policy.

- [ ] **Step 1: Write failing anonymous, CSRF, Host, and pairing tests**

```python
def test_every_mutating_route_rejects_anonymous(app, client):
    for rule in mutating_rules(app.url_map):
        response = invoke_minimal(client, rule)
        assert response.status_code == 401, rule.rule


def test_authenticated_request_requires_csrf_and_allowed_host(authenticated_client):
    assert authenticated_client.post("/shutdown", json={}).status_code == 403
    response = authenticated_client.post(
        "/shutdown", json={}, headers={"X-CSRF-Token": csrf(authenticated_client), "Host": "evil.test"}
    )
    assert response.status_code in {400, 403}


def test_bootstrap_token_is_one_time_and_plaintext_file_is_removed(credentials):
    token = credentials.create_bootstrap_token()
    credentials.consume_bootstrap_token(token, "strong-password")
    assert not credentials.bootstrap_plaintext_path.exists()
    assert not credentials.verify_bootstrap_token(token)
    assert credentials.verify_admin_password("strong-password")
```

- [ ] **Step 2: Run security tests and verify current exposure**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_credentials.py tests\test_auth_blueprint.py tests\test_request_guard.py tests\test_write_route_security.py`

Expected: FAIL because all current write routes are anonymous.

- [ ] **Step 3: Implement credential storage and bounded sessions**

Use Werkzeug `generate_password_hash(method="scrypt")` and `check_password_hash`; persist only hashes and version metadata with mode 0600. The bootstrap plaintext is root-readable 0600, never logged, and deleted after setup. Session stores only admin identity and a random CSRF token.

CredentialStore also implements authenticated password rotation and a root-only recovery-token command. The UI shows a persistent warning when administration is served over plain HTTP; session cookies use `Secure` whenever Flask detects the supported TLS reverse-proxy headers.

- [ ] **Step 4: Install application-wide request guards**

Before each non-GET/HEAD/OPTIONS request: validate Host against configured device names/IPs, require authenticated admin except setup/login allowlist, check Origin/Referer same-origin when present, compare constant-time CSRF header/form token, and apply a bounded per-client/action limiter. JSON failures use stable codes `authentication_required`, `csrf_failed`, `host_not_allowed`, `rate_limited`.

- [ ] **Step 5: Add the browser fetch wrapper**

`inkypi-security.js` reads the CSRF token from a meta tag and injects it into same-origin mutating fetch calls. Load it from the shared/base templates so plugin blueprints inherit coverage.

- [ ] **Step 6: Run all write-route enumeration tests**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_credentials.py tests\test_auth_blueprint.py tests\test_request_guard.py tests\test_write_route_security.py tests\test_plugin_blueprint.py tests\test_apikeys.py`

Expected: PASS for base and dynamically registered plugin blueprints.

- [ ] **Step 7: Commit authentication**

```powershell
git add -- inkypi-weather/package/InkyPi/src/security inkypi-weather/package/InkyPi/src/blueprints/auth.py inkypi-weather/package/InkyPi/src/templates/login.html inkypi-weather/package/InkyPi/src/templates/setup_admin.html inkypi-weather/package/InkyPi/src/static/inkypi-security.js inkypi-weather/package/InkyPi/src/inkypi.py inkypi-weather/package/InkyPi/src/templates inkypi-weather/package/InkyPi/install/bootstrap_admin.py inkypi-weather/package/InkyPi/install/inkypi inkypi-weather/package/InkyPi/tests/test_credentials.py inkypi-weather/package/InkyPi/tests/test_auth_blueprint.py inkypi-weather/package/InkyPi/tests/test_request_guard.py inkypi-weather/package/InkyPi/tests/test_write_route_security.py
git commit -m "feat: authenticate and protect administrative writes"
```

### Task 5: Enforce SSRF-safe browser egress

**Files:**
- Create: `inkypi-weather/package/InkyPi/src/security/ssrf.py`
- Create: `inkypi-weather/package/InkyPi/src/security/egress_proxy.py`
- Modify: `inkypi-weather/package/InkyPi/src/utils/browser_renderer.py`
- Modify: Screenshot, Newspaper, Sports WorldCup, and TechPulse renderer call sites
- Create: `inkypi-weather/package/InkyPi/tests/test_ssrf_policy.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_egress_proxy.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_browser_renderer_security.py`

**Interfaces:**
- Produces: `SSRFPolicy.resolve_and_validate()`, `ApprovedTarget`, loopback egress proxy.
- Consumes: admin host/CIDR allowlist and BrowserRenderer.

- [ ] **Step 1: Write failing rebinding, redirect, IPv6, and metadata tests**

```python
@pytest.mark.parametrize("url", [
    "http://127.0.0.1/", "http://[::1]/", "http://169.254.169.254/latest/meta-data/",
    "http://10.0.0.1/", "http://user:pass@example.com/", "file:///etc/passwd",
])
def test_unsafe_targets_are_rejected(url, policy):
    with pytest.raises(UnsafeTarget):
        policy.resolve_and_validate(url)


def test_redirect_and_dns_rebinding_are_revalidated(proxy, fake_dns):
    fake_dns.sequence("safe.test", ["203.0.113.9", "127.0.0.1"])
    with pytest.raises(UnsafeTarget):
        proxy.fetch("https://safe.test/redirect")
```

- [ ] **Step 2: Run and implement address policy**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_ssrf_policy.py tests\test_egress_proxy.py`

Expected: FAIL on missing modules.

Normalize scheme/host/port, reject userinfo and non-HTTP(S), resolve all A/AAAA addresses, reject loopback/private/link-local/multicast/reserved/metadata and IPv4-mapped IPv6, and return an ApprovedTarget pinned to the validated IP set.

- [ ] **Step 3: Route Chromium through a fail-closed local proxy**

The proxy listens only on an ephemeral loopback socket, validates every request/CONNECT and redirect/subresource resolution, and connects to the approved IP while preserving Host/SNI. BrowserRenderer refuses remote rendering if the proxy is unavailable. Explicit private targets require an authenticated configured allowlist.

- [ ] **Step 4: Run security/browser tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_ssrf_policy.py tests\test_egress_proxy.py tests\test_browser_renderer_security.py tests\test_browser_renderer.py tests\test_tech_pulse.py tests\test_sports_dashboard.py -k "ssrf or browser or screenshot or preview"`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/src/security/ssrf.py inkypi-weather/package/InkyPi/src/security/egress_proxy.py inkypi-weather/package/InkyPi/src/utils/browser_renderer.py inkypi-weather/package/InkyPi/tests/test_ssrf_policy.py inkypi-weather/package/InkyPi/tests/test_egress_proxy.py inkypi-weather/package/InkyPi/tests/test_browser_renderer_security.py
git add -p -- inkypi-weather/package/InkyPi/src/plugins inkypi-weather/package/InkyPi/tests
git commit -m "fix: enforce SSRF-safe browser egress"
```

### Task 6: Run the service unprivileged with a fixed-command broker

**Files:**
- Create: `inkypi-weather/package/InkyPi/install/privileged/inkypi-privileged.socket`
- Create: `inkypi-weather/package/InkyPi/install/privileged/inkypi-privileged.service`
- Create: `inkypi-weather/package/InkyPi/install/privileged/inkypi_privileged.py`
- Create: `inkypi-weather/package/InkyPi/src/utils/privileged_actions.py`
- Modify: `inkypi-weather/package/InkyPi/src/blueprints/settings.py:84-93`
- Modify: `inkypi-weather/package/InkyPi/src/utils/network_utils.py`
- Modify: `inkypi-weather/package/InkyPi/install/inkypi.service`
- Modify: `inkypi-weather/package/InkyPi/install/install.sh`
- Modify: `inkypi-weather/package/InkyPi/install/uninstall.sh`
- Create: `inkypi-weather/package/InkyPi/tests/test_privileged_actions.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_privileged_broker.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_systemd_units.py`

**Interfaces:**
- Produces: four fixed actions `poweroff`, `reboot`, `wifi_powersave_off`, `wifi_reconnect`.
- Consumes: authenticated routes and RuntimePaths ownership.

- [ ] **Step 1: Write failing unit and command-injection tests**

```python
def test_main_unit_is_not_root():
    unit = parse_unit(SERVICE_PATH)
    assert unit["Service"]["User"] == "inkypi"
    assert unit["Service"]["NoNewPrivileges"] == "true"


def test_broker_rejects_unknown_action_and_interface_injection(broker_client):
    assert broker_client.send({"action": "shell", "args": ["id"]}).code == "unknown_action"
    assert broker_client.send({"action": "wifi_reconnect", "interface": "wlan0;reboot"}).code == "invalid_interface"
```

- [ ] **Step 2: Run tests and implement broker protocol**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_privileged_actions.py tests\test_privileged_broker.py tests\test_systemd_units.py`

Expected: FAIL because service runs as root and routes use `os.system`.

Use a Unix stream socket owned root:inkypi 0660. Broker validates peer credentials with `SO_PEERCRED`, accepts one JSON line under 4 KiB, maps a fixed enum to argv-only subprocess calls with timeout, and validates interface against a strict regex plus `/sys/class/net`.

- [ ] **Step 3: Replace privileged Web/runtime calls**

`settings.shutdown()` calls client functions; Wi-Fi helpers call broker actions. Failure returns a diagnostic 503 and never falls back to shell/sudo.

- [ ] **Step 4: Update install ownership and service hardening**

Create `inkypi`, add only present GPIO/SPI/video/render groups, chown `/var/lib/inkypi` and `/var/cache/inkypi`, keep `/opt` root-owned, install broker units, and set the main service to `User=inkypi`, fixed current path, EnvironmentFile, `NoNewPrivileges=true`.

- [ ] **Step 5: Run tests and commit**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_privileged_actions.py tests\test_privileged_broker.py tests\test_systemd_units.py tests\test_network_utils.py`

Expected: PASS.

```powershell
git add -- inkypi-weather/package/InkyPi/install/privileged inkypi-weather/package/InkyPi/src/utils/privileged_actions.py inkypi-weather/package/InkyPi/src/blueprints/settings.py inkypi-weather/package/InkyPi/src/utils/network_utils.py inkypi-weather/package/InkyPi/install/inkypi.service inkypi-weather/package/InkyPi/install/install.sh inkypi-weather/package/InkyPi/install/uninstall.sh inkypi-weather/package/InkyPi/tests/test_privileged_actions.py inkypi-weather/package/InkyPi/tests/test_privileged_broker.py inkypi-weather/package/InkyPi/tests/test_systemd_units.py
git commit -m "feat: run InkyPi behind a minimal privileged broker"
```

### Task 7: Implement journaled release install, health commit, and rollback

**Files:**
- Create: `inkypi-weather/package/InkyPi/install/lib/release_state.py`
- Create: `inkypi-weather/package/InkyPi/install/preflight.py`
- Create: `inkypi-weather/package/InkyPi/install/inkypi-update`
- Modify: `inkypi-weather/package/InkyPi/install/install.sh`
- Modify: `inkypi-weather/package/InkyPi/install/update.sh`
- Modify: `inkypi-weather/package/InkyPi/install/bootstrap.sh`
- Modify: `inkypi-weather/package/InkyPi/install/healthcheck.sh`
- Modify: `inkypi-weather/package/InkyPi/install/update_vendors.sh`
- Modify: `inkypi-weather/package/InkyPi/install/uninstall.sh`
- Modify: `tools/epaperpod-deploy-zip.ps1`
- Create: `inkypi-weather/package/InkyPi/tests/test_release_state.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_install_update.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_uninstall_preserves_data.py`

**Interfaces:**
- Produces: `UpdatePhase`, `ReleaseLayout`, `UpdateJournal`, `atomic_symlink()`, `recover_incomplete_update()`.
- Consumes: RuntimePaths, target release ID from `/readyz`, and new systemd units.

- [ ] **Step 1: Write failing phase/recovery tests**

```python
def test_update_phase_transitions_are_strict(tmp_path):
    journal = UpdateJournal.create(tmp_path / "update-state.json", release_id="new")
    journal.transition(UpdatePhase.DOWNLOADED)
    with pytest.raises(InvalidTransition):
        journal.transition(UpdatePhase.HEALTHY)


@pytest.mark.parametrize("phase, expected", [
    (UpdatePhase.PREFLIGHTED, RecoveryAction.CLEAN_STAGING),
    (UpdatePhase.SWITCHED, RecoveryAction.ROLL_BACK),
    (UpdatePhase.STARTING, RecoveryAction.ROLL_BACK),
    (UpdatePhase.HEALTHY, RecoveryAction.FINISH_COMMIT),
])
def test_power_loss_recovery_is_deterministic(tmp_path, phase, expected):
    journal = journal_at(tmp_path, phase)
    assert journal.recovery_action() is expected
```

- [ ] **Step 2: Run tests and implement durable state machine**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_release_state.py`

Expected: FAIL on missing module.

Implement exact forward states `DOWNLOADED → PREFLIGHTED → SWITCHED → STARTING → HEALTHY → COMMITTED`; any switched/starting failure goes through `ROLLING_BACK → ROLLED_BACK|ROLLBACK_FAILED`. Persist journal before each external side effect with atomic JSON and directory fsync.

- [ ] **Step 3: Implement two-phase update flow**

Preflight verifies SHA256, disk reserve, imports, config-copy migration, static files, and no-hardware start without taking port/SPI. Then stop old service, atomically update `previous/current`, install/restore unit transactionally, daemon-reload, start target, and poll loopback `/readyz` for up to 120 seconds requiring the target release ID. Any failure restores links, unit, config pointer, and old service.

- [ ] **Step 4: Make shell wrappers strict and propagate failures**

Add `set -Eeuo pipefail`; every background command is followed by `wait "$pid"`. Remove `|| true` from restart/health. `uninstall` preserves `/etc/inkypi` and `/var/lib/inkypi` unless `--purge` is explicitly confirmed.

- [ ] **Step 5: Replace direct HTTP zip deployment**

PowerShell computes SHA256 locally, uploads artifact over pinned SSH host key, and invokes `inkypi-update --artifact ... --sha256 ... --release-id ...`. It never uses remote HTTP or `unzip -o` over current.

Vendor downloads use HTTPS, an immutable upstream commit/version, `curl --fail`, and a checked SHA256 before installation. Mutable Waveshare `master` archives and unversioned Chart.js URLs are forbidden by a release-state test.

- [ ] **Step 6: Run failure-injection tests and shell syntax**

Run: `.\tools\run_inkypi_tests.ps1 -q tests\test_release_state.py tests\test_install_update.py tests\test_uninstall_preserves_data.py`

Run: `bash -n inkypi-weather/package/InkyPi/install/*.sh`

Expected: PASS; injected download, pip, migration, daemon-reload, start, readyz, disk, and switch failures retain the old release/config.

- [ ] **Step 7: Commit the updater**

```powershell
git add -- inkypi-weather/package/InkyPi/install/lib/release_state.py inkypi-weather/package/InkyPi/install/preflight.py inkypi-weather/package/InkyPi/install/inkypi-update inkypi-weather/package/InkyPi/install/install.sh inkypi-weather/package/InkyPi/install/update.sh inkypi-weather/package/InkyPi/install/bootstrap.sh inkypi-weather/package/InkyPi/install/healthcheck.sh inkypi-weather/package/InkyPi/install/update_vendors.sh inkypi-weather/package/InkyPi/install/uninstall.sh tools/epaperpod-deploy-zip.ps1 inkypi-weather/package/InkyPi/tests/test_release_state.py inkypi-weather/package/InkyPi/tests/test_install_update.py inkypi-weather/package/InkyPi/tests/test_uninstall_preserves_data.py
git commit -m "feat: install releases transactionally with rollback"
```

### Task 8: Align Python, clean snapshots, and CI release gates

**Files:**
- Create: `.python-version`
- Modify: `devbox.json`
- Create: `inkypi-weather/package/InkyPi/install/requirements-base.in`
- Create: `inkypi-weather/package/InkyPi/install/requirements-pi.in`
- Create: `inkypi-weather/package/InkyPi/install/requirements-dev.in`
- Regenerate: requirements `.txt` files with hashes
- Modify: `scripts/venv.sh`
- Modify: `tools/run_inkypi_tests.ps1`
- Create: `tools/verify_clean_archive.py`
- Modify: `inkypi-weather/package/InkyPi/tests/test_daily_wiki_page.py`
- Modify: `.github/workflows/test.yml`
- Create: `.github/workflows/security.yml`
- Modify: `.gitattributes`
- Modify: `inkypi-weather/package/InkyPi/docs/troubleshooting.md`
- Create: `inkypi-weather/package/InkyPi/tests/test_requirements_contract.py`
- Create: `inkypi-weather/package/InkyPi/tests/test_clean_archive_tool.py`

**Interfaces:**
- Produces: one Python 3.11 development/release baseline and a reproducible clean-archive gate.
- Consumes: all previous plans and tests.

- [ ] **Step 1: Fix the private-font false green with a failing clean-tree test**

Create a fixture font candidate for the Microsoft YaHei priority test; without the fixture, assert the tracked Noto fallback. Run the isolated test after temporarily hiding ignored fonts and confirm both paths pass.

- [ ] **Step 2: Make the test runner clean up after itself**

Require Python 3.11, set `PYTHONDONTWRITEBYTECODE=1`, and remove only the current process temp directory in `finally`. Do not delete pre-existing `.tmp`, venvs, caches, or user files.

- [ ] **Step 3: Add deterministic clean-archive verification**

`verify_clean_archive.py` creates `git archive HEAD` under system temp, runs tests with an external Python 3.11 environment, reports ignored-dependency failures, and deletes only its own temp tree.

- [ ] **Step 4: Generate hashed constraints and validate them**

Use three `.in` files and pip-tools to produce base/Pi/dev lock files with exact transitive versions and hashes. Installer uses `--require-hashes`; aarch64 resolution runs separately where Pi wheels are available.

- [ ] **Step 5: Harden CI**

Pin Actions by commit SHA, set `permissions: contents: read` and job timeouts. Jobs: Python 3.11 full/clean archive, fatal Ruff plus no-new-debt checks, shell/state-machine tests, lock/hash validation, pip-audit, secret scan, and aarch64 dependency resolution. A 30-minute simulated soak is nightly/manual, not every PR.

- [ ] **Step 6: Fix line endings and stale operations docs**

Add LF rules for `*.service` and extensionless install entry scripts. Replace unsupported `inkypi -d` troubleshooting commands with the actual wrapper interface.

- [ ] **Step 7: Run the complete local release gate**

Run:

```powershell
.\tools\run_inkypi_tests.ps1 -q
inkypi-weather\package\InkyPi\.venv\Scripts\python.exe -m ruff check --no-cache --select E9,F63,F7,F82 inkypi-weather\package\InkyPi\src inkypi-weather\package\InkyPi\tests
python tools\verify_clean_archive.py --pytest-args=-q
git diff --check
```

Expected: all tests pass in workspace and archive, Ruff exits 0, and no whitespace errors are reported.

- [ ] **Step 8: Commit release engineering gates**

```powershell
git add -- .python-version devbox.json scripts/venv.sh tools/run_inkypi_tests.ps1 tools/verify_clean_archive.py .github/workflows/test.yml .github/workflows/security.yml .gitattributes inkypi-weather/package/InkyPi/install/requirements-base.in inkypi-weather/package/InkyPi/install/requirements-pi.in inkypi-weather/package/InkyPi/install/requirements-dev.in inkypi-weather/package/InkyPi/install/requirements-base.txt inkypi-weather/package/InkyPi/install/requirements.txt inkypi-weather/package/InkyPi/install/requirements-dev.txt inkypi-weather/package/InkyPi/tests/test_daily_wiki_page.py inkypi-weather/package/InkyPi/tests/test_requirements_contract.py inkypi-weather/package/InkyPi/tests/test_clean_archive_tool.py inkypi-weather/package/InkyPi/docs/troubleshooting.md
git commit -m "ci: enforce reproducible clean release gates"
```

### Task 9: Final operations and security gate

**Files:**
- Modify only when the final gate exposes a defect.

**Interfaces:**
- Consumes: all four implementation plans.
- Produces: local release evidence and a list of device-only checks awaiting deployment authorization.

- [ ] **Step 1: Run all automated tests and static contracts**

Run: `.\tools\run_inkypi_tests.ps1 -q`

Expected: all tests pass with no skipped clean-tree dependency.

- [ ] **Step 2: Run script and artifact checks**

Run Bash syntax, `git diff --check`, generated SecretSchema artifacts, hashed lock validation, systemd unit parsing, and clean archive verification.

- [ ] **Step 3: Run the simulated soak**

Run the 30-minute fake display/network scheduler soak. After warm-up, RSS must not grow linearly, managed disk returns under configured budgets, Chromium/child PID count returns to zero, and retry intervals follow 30/60/120/300 seconds.

- [ ] **Step 4: Produce device verification checklist without deploying**

List exact pending device evidence: dedicated user/groups, broker socket, systemd stop under 240 seconds, BUSY timeout, readyz release ID, rollback drill, restart/OOM counters, physical current-image match, and 24-hour soak. Mark all as unverified until the user authorizes deployment.

- [ ] **Step 5: Commit only actual final-gate fixes**

If no defect was found, create no empty commit. If fixes were necessary, stage explicit files, rerun the affected and full gates, and commit as `fix: close release gate regressions`.
