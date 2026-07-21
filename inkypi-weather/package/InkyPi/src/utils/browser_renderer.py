"""Single-flight, bounded Chromium rendering with deterministic cleanup."""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import logging
import math
import os
from pathlib import Path
import shutil
import signal
import stat
import subprocess
import tempfile
import threading
import time
from urllib.parse import urlsplit, urlunsplit
import weakref

try:
    from ..runtime.cache_lifecycle import (
        CleanupBudget,
        LifecycleAggregate,
        LifecycleAllowance,
        LifecycleBudget,
    )
except ImportError:  # pragma: no cover - production imports modules from src/
    from runtime.cache_lifecycle import (
        CleanupBudget,
        LifecycleAggregate,
        LifecycleAllowance,
        LifecycleBudget,
    )
from runtime.refresh_contracts import TaskCancelled, TaskContext
from security.egress_proxy import EgressProxy
from security.ssrf import ApprovedTarget, UnsafeTarget, get_ssrf_policy
from utils.safe_image import safe_open_image


logger = logging.getLogger(__name__)

RENDERER_VERSION = "browser-renderer-v2-ssrf-proxy"
DEFAULT_TIMEOUT_SECONDS = 60.0
DEFAULT_VIRTUAL_TIME_BUDGET_MS = 2_000
NEGATIVE_CACHE_TTL_SECONDS = 600.0
HTML_CIRCUIT_TTL_SECONDS = 300.0
MAX_NEGATIVE_CACHE_ENTRIES = 256
MAX_HTML_BYTES = 5 * 1024 * 1024
_GLOBAL_BROWSER_SLOT = threading.Semaphore(1)
_GLOBAL_RENDERER = None
_GLOBAL_RENDERER_LOCK = threading.Lock()


def _lifecycle_allowance(*, budget, allowance, aggregate, clock):
    if allowance is not None:
        if not isinstance(allowance, LifecycleAllowance):
            raise TypeError("allowance must be a LifecycleAllowance")
        if aggregate is not None and allowance.aggregate is not aggregate:
            raise ValueError("allowance and aggregate must share the same counters")
        return allowance
    if isinstance(budget, CleanupBudget):
        budget = budget.start(clock())
    if not isinstance(budget, LifecycleBudget):
        raise TypeError("budget must be a CleanupBudget or LifecycleBudget")
    if aggregate is None:
        aggregate = LifecycleAggregate()
    elif not isinstance(aggregate, LifecycleAggregate):
        raise TypeError("aggregate must be a LifecycleAggregate")
    return LifecycleAllowance(budget, aggregate, clock=clock)


def _is_reparse_point(info):
    attributes = int(getattr(info, "st_file_attributes", 0) or 0)
    marker = int(getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    return bool(attributes & marker)


def _stat_token(info):
    return (
        int(info.st_dev),
        int(info.st_ino),
        int(info.st_size),
        int(info.st_mtime_ns),
    )


def _browser_job_tree_size(path, *, allowance):
    total_bytes = 0
    scanned_entries = 0
    stack = [Path(path)]
    while stack:
        directory = stack.pop()
        try:
            iterator = os.scandir(directory)
        except FileNotFoundError:
            return total_bytes, scanned_entries, False, False, False
        except OSError:
            return total_bytes, scanned_entries, False, False, True
        with iterator:
            for entry in iterator:
                if not allowance.consume_scan():
                    return total_bytes, scanned_entries, False, False, False
                scanned_entries += 1
                try:
                    info = os.lstat(entry.path)
                except FileNotFoundError:
                    return total_bytes, scanned_entries, False, False, False
                except OSError:
                    return total_bytes, scanned_entries, False, False, True
                if _is_reparse_point(info) or stat.S_ISLNK(info.st_mode):
                    return total_bytes, scanned_entries, False, True, False
                if stat.S_ISDIR(info.st_mode):
                    stack.append(Path(entry.path))
                elif stat.S_ISREG(info.st_mode):
                    total_bytes += max(0, int(info.st_size))
                else:
                    return total_bytes, scanned_entries, False, True, False
    return total_bytes, scanned_entries, True, False, False


def find_browser_binary():
    candidates = ["chromium-headless-shell", "chromium", "google-chrome", "chrome"]
    if os.name == "nt":
        candidates.extend(
            [
                r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
                r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
                r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            ]
        )
    for candidate in candidates:
        if os.path.isabs(candidate) and os.path.isfile(candidate):
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return None


def _safe_target(value):
    parsed = urlsplit(str(value or ""))
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, "", ""))


def _cache_target(value):
    parsed = urlsplit(str(value or ""))
    query_hash = (
        hashlib.sha256(parsed.query.encode("utf-8")).hexdigest()
        if parsed.query
        else ""
    )
    return f"{urlunsplit((parsed.scheme, parsed.netloc, parsed.path, '', ''))}|{query_hash}"


class BrowserRenderer:
    """Render local HTML or validated URLs through one global Chromium slot."""

    def __init__(
        self,
        *,
        binary=None,
        temp_root=None,
        popen=subprocess.Popen,
        clock=time.monotonic,
        negative_ttl_seconds=NEGATIVE_CACHE_TTL_SECONDS,
        html_circuit_ttl_seconds=HTML_CIRCUIT_TTL_SECONDS,
        run_as_root=None,
        ssrf_policy=None,
        egress_proxy=None,
    ):
        self.binary = binary or find_browser_binary()
        configured_root = os.getenv("INKYPI_BROWSER_TEMP_DIR", "").strip()
        self.temp_root = Path(
            temp_root
            or configured_root
            or (Path(tempfile.gettempdir()) / "inkypi-browser-jobs")
        )
        self.temp_root.mkdir(parents=True, exist_ok=True)
        self._popen = popen
        self._clock = clock
        self.negative_ttl_seconds = max(0.0, float(negative_ttl_seconds))
        self.html_circuit_ttl_seconds = max(0.0, float(html_circuit_ttl_seconds))
        if run_as_root is None:
            get_euid = getattr(os, "geteuid", None)
            run_as_root = callable(get_euid) and get_euid() == 0
        self.run_as_root = bool(run_as_root)
        self._negative_cache = {}
        self._negative_lock = threading.Lock()
        self._html_circuit_until = 0.0
        self._html_circuit_lock = threading.Lock()
        self._processes = {}
        self._process_lock = threading.Lock()
        self.ssrf_policy = ssrf_policy or get_ssrf_policy()
        self.egress_proxy = egress_proxy or EgressProxy(policy=self.ssrf_policy)
        self._proxy_finalizer = weakref.finalize(self, self.egress_proxy.close)
        self._closed = False

    @property
    def closed(self):
        return self._closed

    @property
    def active_processes(self):
        with self._process_lock:
            return tuple(sorted(self._processes))

    @property
    def negative_cache_size(self):
        with self._negative_lock:
            self._prune_negative_locked(self._clock())
            return len(self._negative_cache)

    def render_html(
        self,
        html,
        *,
        viewport,
        context=None,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        timezone_name=None,
    ):
        if not isinstance(html, str):
            raise TypeError("html must be a string")
        encoded = html.encode("utf-8")
        if len(encoded) > MAX_HTML_BYTES:
            logger.warning("Browser HTML input exceeded %s bytes", MAX_HTML_BYTES)
            return None
        if self._html_circuit_open():
            return None
        key = self._cache_key("html", hashlib.sha256(encoded).hexdigest(), viewport)
        if self._negative_hit(key):
            return None
        context = self._context(context, timeout_seconds)

        def prepare(job_dir):
            html_path = job_dir / "input.html"
            html_path.write_bytes(encoded)
            return html_path.resolve().as_uri()

        return self._render(
            key,
            prepare,
            viewport=viewport,
            context=context,
            timeout_seconds=timeout_seconds,
            timezone_name=timezone_name,
            failure_scope="html",
        )

    def render_url(
        self,
        url,
        *,
        viewport,
        context=None,
        validator=None,
        timeout_seconds=DEFAULT_TIMEOUT_SECONDS,
        timezone_name=None,
    ):
        if not callable(validator):
            logger.warning("Rejected unvalidated browser URL: %s", _safe_target(url))
            return None
        try:
            validated = validator(str(url))
        except Exception:
            logger.warning("Browser URL validator failed: %s", _safe_target(url))
            return None
        if isinstance(validated, ApprovedTarget) or isinstance(
            getattr(validated, "normalized_url", None),
            str,
        ):
            target = validated.normalized_url
        elif validated is True:
            target = str(url)
        elif isinstance(validated, str) and validated.strip():
            target = validated.strip()
        else:
            logger.warning("Browser URL validator rejected: %s", _safe_target(url))
            return None
        scheme = urlsplit(target).scheme.lower()
        if scheme not in {"http", "https"}:
            return None
        try:
            approved = self.ssrf_policy.resolve_and_validate(target)
        except (UnsafeTarget, OSError, ValueError):
            logger.warning("Browser SSRF policy rejected: %s", _safe_target(target))
            return None
        target = approved.normalized_url
        key = self._cache_key("url", _cache_target(target), viewport)
        if self._negative_hit(key):
            return None
        context = self._context(context, timeout_seconds)
        return self._render(
            key,
            lambda _job_dir: target,
            viewport=viewport,
            context=context,
            timeout_seconds=timeout_seconds,
            timezone_name=timezone_name,
            failure_scope="url",
        )

    def close(self):
        self._closed = True
        with self._process_lock:
            processes = tuple(self._processes.values())
        for process in processes:
            self._stop_process(process)
        if self._proxy_finalizer.alive:
            try:
                self._proxy_finalizer()
            except Exception:
                logger.exception("Browser egress proxy did not close cleanly")

    def cleanup_abandoned_jobs(
        self,
        *,
        now_epoch,
        stale_seconds,
        budget=None,
        dry_run=False,
        allowance=None,
        aggregate=None,
    ):
        """Recover stale direct-child render jobs without racing Chromium."""

        allowance = _lifecycle_allowance(
            budget=budget,
            allowance=allowance,
            aggregate=aggregate,
            clock=self._clock,
        )
        aggregate = allowance.aggregate
        try:
            now_epoch = float(now_epoch)
            stale_seconds = float(stale_seconds)
        except (TypeError, ValueError, OverflowError):
            raise ValueError("cleanup times must be finite numbers") from None
        if not math.isfinite(now_epoch) or not math.isfinite(stale_seconds):
            raise ValueError("cleanup times must be finite numbers")
        stale_seconds = max(0.0, stale_seconds)

        slot = _GLOBAL_BROWSER_SLOT
        acquired = slot.acquire(blocking=False)
        if not acquired:
            allowance.mark_backlog()
            return aggregate
        try:
            if self.active_processes:
                allowance.mark_backlog()
                return aggregate
            if not allowance.can_delete(0):
                return aggregate
            try:
                root_info = os.lstat(self.temp_root)
                if (
                    not stat.S_ISDIR(root_info.st_mode)
                    or stat.S_ISLNK(root_info.st_mode)
                    or _is_reparse_point(root_info)
                ):
                    aggregate.skipped_unsafe += 1
                    allowance.mark_backlog()
                    return aggregate
                root = self.temp_root.resolve(strict=True)
            except FileNotFoundError:
                return aggregate
            except OSError:
                aggregate.error_count += 1
                allowance.mark_backlog()
                return aggregate

            names = []
            try:
                with os.scandir(root) as iterator:
                    while True:
                        try:
                            entry = next(iterator)
                        except StopIteration:
                            break
                        if not allowance.consume_scan():
                            return aggregate
                        names.append(entry.name)
            except OSError:
                aggregate.error_count += 1
                allowance.mark_backlog()
                return aggregate
            names.sort(
                key=lambda name: (
                    not name.startswith(".gc-render-"),
                    name,
                )
            )
            baseline_deleted_entries = aggregate.deleted_entries
            baseline_deleted_bytes = aggregate.deleted_bytes
            planned_entries = 0
            planned_bytes = 0
            for name in names:
                if not allowance.can_delete(0):
                    break
                is_tombstone = name.startswith(".gc-render-") and len(name) > len(
                    ".gc-render-"
                )
                is_render = name.startswith("render-") and len(name) > len("render-")
                if not is_tombstone and not is_render:
                    aggregate.skipped_unsafe += 1
                    continue

                candidate = root / name
                try:
                    info = os.lstat(candidate)
                    if (
                        not stat.S_ISDIR(info.st_mode)
                        or stat.S_ISLNK(info.st_mode)
                        or _is_reparse_point(info)
                        or candidate.resolve(strict=True).parent != root
                    ):
                        aggregate.skipped_unsafe += 1
                        continue
                except FileNotFoundError:
                    continue
                except OSError:
                    aggregate.error_count += 1
                    allowance.mark_backlog()
                    continue
                if is_render and now_epoch - float(info.st_mtime) <= stale_seconds:
                    aggregate.retained_recent += 1
                    continue

                size, _nested_scanned, complete, unsafe, failed = (
                    _browser_job_tree_size(
                        candidate,
                        allowance=allowance,
                    )
                )
                if failed:
                    aggregate.error_count += 1
                    allowance.mark_backlog()
                    continue
                if unsafe:
                    aggregate.skipped_unsafe += 1
                    continue
                if not complete:
                    allowance.mark_backlog()
                    break

                if (
                    baseline_deleted_entries + planned_entries
                    >= allowance.budget.max_deleted
                    or baseline_deleted_bytes + planned_bytes + size
                    > allowance.budget.max_deleted_bytes
                ):
                    allowance.mark_backlog()
                    break
                if not allowance.can_delete(0):
                    break

                planned_entries += 1
                planned_bytes += size
                aggregate.candidate_entries += 1
                if dry_run:
                    continue

                expected_token = _stat_token(info)
                removal_path = candidate
                try:
                    current_info = os.lstat(candidate)
                    if (
                        _stat_token(current_info) != expected_token
                        or not stat.S_ISDIR(current_info.st_mode)
                        or stat.S_ISLNK(current_info.st_mode)
                        or _is_reparse_point(current_info)
                        or candidate.resolve(strict=True).parent != root
                    ):
                        aggregate.skipped_unsafe += 1
                        allowance.mark_backlog()
                        continue
                    if is_render:
                        removal_path = root / f".gc-{name}"
                        try:
                            os.lstat(removal_path)
                        except FileNotFoundError:
                            pass
                        else:
                            aggregate.skipped_unsafe += 1
                            allowance.mark_backlog()
                            continue
                        os.rename(candidate, removal_path)
                    renamed_info = os.lstat(removal_path)
                    if (
                        _stat_token(renamed_info) != expected_token
                        or not stat.S_ISDIR(renamed_info.st_mode)
                        or stat.S_ISLNK(renamed_info.st_mode)
                        or _is_reparse_point(renamed_info)
                        or removal_path.resolve(strict=True).parent != root
                    ):
                        aggregate.skipped_unsafe += 1
                        allowance.mark_backlog()
                        continue
                    shutil.rmtree(removal_path)
                except FileNotFoundError:
                    continue
                except OSError:
                    aggregate.error_count += 1
                    allowance.mark_backlog()
                    continue
                allowance.consume_delete(size)
            return aggregate
        finally:
            slot.release()

    def _render(
        self,
        key,
        prepare_target,
        *,
        viewport,
        context,
        timeout_seconds,
        timezone_name,
        failure_scope,
    ):
        if self._closed or not self.binary:
            self._remember_negative(key)
            return None
        width, height = self._viewport(viewport)
        job_dir = None
        process = None
        try:
            with self._browser_slot(context):
                context.raise_if_cancelled()
                if not self.egress_proxy.start():
                    logger.error("Browser render refused because egress proxy is unavailable")
                    self._remember_negative(key)
                    return None
                proxy_url = self.egress_proxy.proxy_url
                if not proxy_url:
                    logger.error("Browser render refused because egress proxy has no endpoint")
                    self._remember_negative(key)
                    return None
                job_dir = Path(
                    tempfile.mkdtemp(prefix="render-", dir=self.temp_root)
                )
                profile_dir = job_dir / "profile"
                profile_dir.mkdir()
                output_path = job_dir / "screenshot.png"
                target = prepare_target(job_dir)
                command = self._command(
                    target,
                    output_path,
                    profile_dir,
                    (width, height),
                    timeout_seconds,
                    timezone_name,
                    proxy_url,
                )
                popen_kwargs = {
                    "stdin": subprocess.DEVNULL,
                    "stdout": subprocess.DEVNULL,
                    "stderr": subprocess.DEVNULL,
                }
                if os.name != "nt":
                    popen_kwargs["start_new_session"] = True
                process = self._popen(command, **popen_kwargs)
                self._register_process(process)
                try:
                    wait_timeout = min(
                        self._timeout(timeout_seconds),
                        context.remaining_seconds(),
                    )
                    if wait_timeout <= 0:
                        context.raise_if_cancelled()
                    process.wait(timeout=max(0.001, wait_timeout))
                except subprocess.TimeoutExpired:
                    logger.warning("Chromium render timed out for %s", _safe_target(target))
                    self._stop_process(process)
                    self._remember_negative(key)
                    if failure_scope == "html":
                        self._remember_html_failure()
                    return None
                finally:
                    self._unregister_process(process)
                context.raise_if_cancelled()
                if process.returncode != 0 or not output_path.is_file():
                    self._remember_negative(key)
                    if failure_scope == "html":
                        self._remember_html_failure()
                    return None
                image = safe_open_image(output_path)
                self._forget_negative(key)
                if failure_scope == "html":
                    self._forget_html_failure()
                return image
        except TaskCancelled:
            if process is not None and process.poll() is None:
                self._stop_process(process)
            self._remember_negative(key)
            return None
        except Exception:
            if process is not None and process.poll() is None:
                self._stop_process(process)
            logger.exception("Chromium render failed")
            self._remember_negative(key)
            if failure_scope == "html":
                self._remember_html_failure()
            return None
        finally:
            if process is not None:
                self._unregister_process(process)
            if job_dir is not None:
                shutil.rmtree(job_dir, ignore_errors=True)

    @contextmanager
    def _browser_slot(self, context):
        acquired = False
        try:
            while not acquired:
                context.raise_if_cancelled()
                remaining = context.remaining_seconds()
                acquired = _GLOBAL_BROWSER_SLOT.acquire(
                    timeout=max(0.001, min(0.05, remaining))
                )
            yield
        finally:
            if acquired:
                _GLOBAL_BROWSER_SLOT.release()

    def _command(
        self,
        target,
        output_path,
        profile_dir,
        viewport,
        timeout_seconds,
        timezone_name,
        proxy_url,
    ):
        timeout_ms = int(self._timeout(timeout_seconds) * 1000)
        # Chromium does not take a screenshot until its virtual-time budget is
        # exhausted. Coupling this value to the outer process timeout made a
        # nominal 60-second render wait the full 60 seconds and then lose the
        # race against process.wait(). Two virtual seconds is sufficient for
        # local fonts, images, and Chart.js while leaving ample real time for
        # startup on low-memory display hardware.
        virtual_time_budget_ms = min(timeout_ms, DEFAULT_VIRTUAL_TIME_BUDGET_MS)
        command = [
            str(self.binary),
            "--headless",
            f"--screenshot={output_path}",
            f"--window-size={viewport[0]},{viewport[1]}",
            f"--user-data-dir={profile_dir}",
            "--disable-background-networking",
            "--disable-default-apps",
            "--disable-dev-shm-usage",
            "--disable-extensions",
            "--disable-gpu",
            "--disable-gpu-memory-buffer-compositor-resources",
            "--disable-plugins",
            "--disable-quic",
            "--disable-zero-copy",
            "--disable-sync",
            "--disable-features=Translate,DownloadBubble,OptimizationHints,DnsOverHttps",
            "--force-webrtc-ip-handling-policy=disable_non_proxied_udp",
            "--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1",
            "--hide-scrollbars",
            "--in-process-gpu",
            "--js-flags=--jitless",
            "--mute-audio",
            "--no-first-run",
            "--renderer-process-limit=1",
            "--use-gl=swiftshader",
            "--disk-cache-size=1",
            "--media-cache-size=1",
            f"--proxy-server={proxy_url}",
            "--proxy-bypass-list=<-loopback>",
            f"--timeout={timeout_ms}",
            f"--virtual-time-budget={virtual_time_budget_ms}",
        ]
        if self.run_as_root:
            command.append("--no-sandbox")
        if timezone_name:
            command.append(f"--timezone-for-testing={str(timezone_name)[:80]}")
        command.append(str(target))
        return command

    def _register_process(self, process):
        with self._process_lock:
            self._processes[int(process.pid)] = process

    def _unregister_process(self, process):
        with self._process_lock:
            self._processes.pop(int(process.pid), None)

    def _stop_process(self, process):
        if process.poll() is not None:
            return
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGTERM)
            else:
                process.terminate()
            process.wait(timeout=2)
            return
        except (OSError, subprocess.TimeoutExpired):
            pass
        try:
            if os.name != "nt":
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            logger.warning("Chromium process did not exit cleanly: %s", process.pid)

    def _negative_hit(self, key):
        now = self._clock()
        with self._negative_lock:
            self._prune_negative_locked(now)
            expires_at = self._negative_cache.get(key)
            return expires_at is not None and expires_at > now

    def _remember_negative(self, key):
        now = self._clock()
        with self._negative_lock:
            self._prune_negative_locked(now)
            self._negative_cache[key] = now + self.negative_ttl_seconds
            while len(self._negative_cache) > MAX_NEGATIVE_CACHE_ENTRIES:
                oldest = min(self._negative_cache, key=self._negative_cache.get)
                self._negative_cache.pop(oldest, None)

    def _forget_negative(self, key):
        with self._negative_lock:
            self._negative_cache.pop(key, None)

    def _html_circuit_open(self):
        with self._html_circuit_lock:
            return self._html_circuit_until > self._clock()

    def _remember_html_failure(self):
        with self._html_circuit_lock:
            self._html_circuit_until = self._clock() + self.html_circuit_ttl_seconds

    def _forget_html_failure(self):
        with self._html_circuit_lock:
            self._html_circuit_until = 0.0

    def _prune_negative_locked(self, now):
        self._negative_cache = {
            key: expires
            for key, expires in self._negative_cache.items()
            if expires > now
        }

    @staticmethod
    def _cache_key(kind, target, viewport):
        width, height = BrowserRenderer._viewport(viewport)
        raw = f"{RENDERER_VERSION}|{kind}|{target}|{width}x{height}"
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def _viewport(viewport):
        if not isinstance(viewport, (tuple, list)) or len(viewport) != 2:
            raise ValueError("viewport must contain width and height")
        width, height = (int(viewport[0]), int(viewport[1]))
        if not 1 <= width <= 8192 or not 1 <= height <= 8192:
            raise ValueError("viewport dimensions are out of range")
        return width, height

    @staticmethod
    def _timeout(value):
        try:
            timeout = float(value)
        except (TypeError, ValueError, OverflowError):
            timeout = DEFAULT_TIMEOUT_SECONDS
        return max(0.01, min(180.0, timeout))

    @staticmethod
    def _context(context, timeout_seconds):
        if context is not None:
            return context
        return TaskContext.never_cancelled(
            deadline_monotonic=time.monotonic()
            + BrowserRenderer._timeout(timeout_seconds),
        )


def get_browser_renderer():
    global _GLOBAL_RENDERER

    renderer = _GLOBAL_RENDERER
    if renderer is None or renderer.closed:
        with _GLOBAL_RENDERER_LOCK:
            if _GLOBAL_RENDERER is None or _GLOBAL_RENDERER.closed:
                _GLOBAL_RENDERER = BrowserRenderer()
            renderer = _GLOBAL_RENDERER
    return renderer


def close_browser_renderer():
    global _GLOBAL_RENDERER

    with _GLOBAL_RENDERER_LOCK:
        renderer = _GLOBAL_RENDERER
        _GLOBAL_RENDERER = None
    if renderer is not None:
        renderer.close()
