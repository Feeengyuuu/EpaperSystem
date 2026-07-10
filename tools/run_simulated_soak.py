#!/usr/bin/env python3
"""Exercise bounded scheduler resources with fake display and network traffic."""

import argparse
import gc
import hashlib
import json
import math
import sys
import tempfile
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import median

import psutil
from PIL import Image


REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = REPO_ROOT / "inkypi-weather/package/InkyPi"
SRC_ROOT = PROJECT_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from runtime.scheduler_state import RetryRegistry  # noqa: E402
from utils.cache_manager import CacheBudget, CacheManager  # noqa: E402


EXPECTED_RETRY_DELAYS = (30.0, 60.0, 120.0, 300.0)
CACHE_MAX_FILES = 8
CACHE_MAX_BYTES = 24 * 1024
RSS_MIN_GROWTH_LIMIT_BYTES = 32 * 1024 * 1024
RSS_LINEAR_GROWTH_FLOOR_BYTES = 4 * 1024 * 1024
RSS_LINEAR_R_SQUARED_THRESHOLD = 0.90


class SoakFailure(RuntimeError):
    """Raised when the simulated scheduler violates a resource invariant."""


@dataclass(frozen=True)
class SoakReport:
    requested_duration_seconds: float
    elapsed_seconds: float
    iterations: int
    retry_delays: tuple[float, ...]
    warmup_rss_bytes: int
    final_rss_bytes: int
    rss_growth_bytes: int
    rss_growth_limit_bytes: int
    rss_slope_bytes_per_sample: float
    rss_trend_r_squared: float
    cache_files: int
    cache_bytes: int
    cache_max_files: int
    cache_max_bytes: int
    cache_evicted_total: int
    child_pids_remaining: tuple[int, ...]
    display_updates: int
    last_display_digest: str


class _FakeNetwork:
    """Fail four calls, then return one bounded payload, repeatedly."""

    def fetch(self, iteration: int) -> bytes:
        if iteration % 5 < 4:
            raise ConnectionError("simulated upstream outage")
        seed = hashlib.sha256(f"fake-network-{iteration}".encode()).digest()
        return (seed * 128)[:4096]


class _FakeDisplay:
    def __init__(self):
        self.updates = 0
        self.last_digest = ""

    def display(self, image: Image.Image) -> None:
        self.last_digest = hashlib.sha256(image.tobytes()).hexdigest()
        self.updates += 1


class _SimulatedScheduler:
    def __init__(self, cache_root: Path):
        self.retry = RetryRegistry(jitter=lambda delay: delay)
        self.network = _FakeNetwork()
        self.display = _FakeDisplay()
        self.cache_manager = CacheManager(
            cache_root,
            global_max_bytes=CACHE_MAX_BYTES,
        )
        self.cache = self.cache_manager.namespace(
            "fake-network",
            CacheBudget(
                max_age_seconds=3600,
                max_files=CACHE_MAX_FILES,
                max_bytes=CACHE_MAX_BYTES,
            ),
        )
        self.retry_delays: list[float] = []

    def cycle(self, iteration: int, now_monotonic: float) -> None:
        try:
            payload = self.network.fetch(iteration)
        except ConnectionError:
            delay = self.retry.mark_failure("fake-network", now_monotonic)
            self.retry_delays.append(delay)
            payload = b"offline"
        else:
            self.retry.mark_success("fake-network")
            self.cache.put_bytes(f"response/{iteration:08d}", payload, suffix=".bin")

        digest = hashlib.sha256(payload).digest()
        color = (digest[0], digest[1], digest[2])
        image = Image.new("RGB", (64, 32), color)
        try:
            self.display.display(image)
        finally:
            image.close()


def _rss_bytes() -> int:
    return int(psutil.Process().memory_info().rss)


def _child_pids() -> tuple[int, ...]:
    process = psutil.Process()
    pids = []
    for child in process.children(recursive=True):
        try:
            if child.is_running() and child.status() != psutil.STATUS_ZOMBIE:
                pids.append(int(child.pid))
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return tuple(sorted(set(pids)))


def _linear_trend(samples: list[int]) -> tuple[float, float]:
    if len(samples) < 3:
        return 0.0, 0.0
    mean_x = (len(samples) - 1) / 2
    mean_y = sum(samples) / len(samples)
    denominator = sum((index - mean_x) ** 2 for index in range(len(samples)))
    slope = sum(
        (index - mean_x) * (value - mean_y)
        for index, value in enumerate(samples)
    ) / denominator
    total_variance = sum((value - mean_y) ** 2 for value in samples)
    if total_variance == 0:
        return slope, 0.0
    residual_variance = sum(
        (value - (mean_y + slope * (index - mean_x))) ** 2
        for index, value in enumerate(samples)
    )
    r_squared = max(0.0, min(1.0, 1.0 - residual_variance / total_variance))
    return slope, r_squared


def _rss_summary(samples: list[int]) -> tuple[int, int, int, int, float, float]:
    if not samples:
        raise SoakFailure("RSS sampling produced no data")
    warmup_index = max(1, len(samples) // 10)
    stabilized = samples[warmup_index:] or samples
    window = max(1, len(stabilized) // 5)
    warmup_rss = int(median(stabilized[:window]))
    final_rss = int(median(stabilized[-window:]))
    growth = max(0, final_rss - warmup_rss)
    limit = max(RSS_MIN_GROWTH_LIMIT_BYTES, int(warmup_rss * 0.25))
    slope, r_squared = _linear_trend(stabilized)
    return warmup_rss, final_rss, growth, limit, slope, r_squared


def run_soak(
    *,
    duration_seconds: float,
    interval_seconds: float = 1.0,
    clock=time.monotonic,
    sleeper=time.sleep,
    rss_reader=_rss_bytes,
    child_pid_reader=_child_pids,
    temp_parent: Path | None = None,
) -> SoakReport:
    duration = float(duration_seconds)
    interval = float(interval_seconds)
    if not math.isfinite(duration) or duration <= 0:
        raise ValueError("duration_seconds must be finite and positive")
    if not math.isfinite(interval) or interval <= 0:
        raise ValueError("interval_seconds must be finite and positive")

    parent = str(Path(temp_parent).resolve()) if temp_parent is not None else None
    rss_samples: list[int] = []
    start = float(clock())
    iterations = 0
    with tempfile.TemporaryDirectory(prefix="inkypi-simulated-soak-", dir=parent) as owned_temp:
        scheduler = _SimulatedScheduler(Path(owned_temp) / "cache")
        while True:
            now = float(clock())
            elapsed = now - start
            if elapsed >= duration:
                break
            scheduler.cycle(iterations, now)
            iterations += 1
            rss_samples.append(int(rss_reader()))
            remaining = duration - (float(clock()) - start)
            if remaining > 0:
                sleeper(min(interval, remaining))

        cache_status = scheduler.cache.maintenance()
        retry_delays = tuple(scheduler.retry_delays)
        display_updates = scheduler.display.updates
        last_display_digest = scheduler.display.last_digest

    gc.collect()
    rss_samples.append(int(rss_reader()))
    (
        warmup_rss,
        final_rss,
        rss_growth,
        rss_limit,
        rss_slope,
        rss_r_squared,
    ) = _rss_summary(rss_samples)
    elapsed = max(0.0, float(clock()) - start)
    return SoakReport(
        requested_duration_seconds=duration,
        elapsed_seconds=elapsed,
        iterations=iterations,
        retry_delays=retry_delays,
        warmup_rss_bytes=warmup_rss,
        final_rss_bytes=final_rss,
        rss_growth_bytes=rss_growth,
        rss_growth_limit_bytes=rss_limit,
        rss_slope_bytes_per_sample=rss_slope,
        rss_trend_r_squared=rss_r_squared,
        cache_files=cache_status.files,
        cache_bytes=cache_status.bytes,
        cache_max_files=CACHE_MAX_FILES,
        cache_max_bytes=CACHE_MAX_BYTES,
        cache_evicted_total=cache_status.evicted_total,
        child_pids_remaining=tuple(child_pid_reader()),
        display_updates=display_updates,
        last_display_digest=last_display_digest,
    )


def validate_report(report: SoakReport) -> None:
    if report.iterations < 5:
        raise SoakFailure("soak did not run enough scheduler cycles to validate retries")
    for index, delay in enumerate(report.retry_delays):
        expected = EXPECTED_RETRY_DELAYS[index % len(EXPECTED_RETRY_DELAYS)]
        if delay != expected:
            raise SoakFailure(
                f"retry delay regression at failure {index + 1}: expected {expected}, got {delay}"
            )
    if len(report.retry_delays) < len(EXPECTED_RETRY_DELAYS):
        raise SoakFailure("soak did not observe the complete 30/60/120/300 retry sequence")
    if (
        report.cache_files > report.cache_max_files
        or report.cache_bytes > report.cache_max_bytes
    ):
        raise SoakFailure(
            "managed cache exceeded its configured file or byte budget"
        )
    if (
        report.rss_growth_bytes >= RSS_LINEAR_GROWTH_FLOOR_BYTES
        and report.rss_slope_bytes_per_sample > 0
        and report.rss_trend_r_squared >= RSS_LINEAR_R_SQUARED_THRESHOLD
    ):
        raise SoakFailure(
            "RSS grew linearly after warm-up: "
            f"growth={report.rss_growth_bytes} bytes, "
            f"r_squared={report.rss_trend_r_squared:.3f}"
        )
    if report.rss_growth_bytes > report.rss_growth_limit_bytes:
        raise SoakFailure(
            "RSS grew beyond the post-warm-up limit: "
            f"{report.rss_growth_bytes} > {report.rss_growth_limit_bytes} bytes"
        )
    if report.child_pids_remaining:
        raise SoakFailure(
            f"child processes remained after soak: {report.child_pids_remaining}"
        )
    if report.display_updates != report.iterations or not report.last_display_digest:
        raise SoakFailure("fake display did not complete every scheduler cycle")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--duration-seconds", type=float, default=1800.0)
    parser.add_argument("--interval-seconds", type=float, default=1.0)
    parser.add_argument("--report", type=Path)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report = run_soak(
            duration_seconds=args.duration_seconds,
            interval_seconds=args.interval_seconds,
        )
        validate_report(report)
    except (SoakFailure, ValueError) as exc:
        print(f"simulated soak failed: {exc}", file=sys.stderr)
        return 1
    payload = json.dumps(asdict(report), indent=2, sort_keys=True)
    print(payload)
    if args.report is not None:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(payload + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
