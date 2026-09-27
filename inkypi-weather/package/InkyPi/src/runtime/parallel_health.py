"""Bounded and sanitized diagnostics for renderer admission and execution."""

import math
from collections.abc import Mapping


def parallel_runtime_health_snapshot(resource_governor, parallel_image_runner):
    """Return bounded aggregate metrics without child or instance identity."""

    try:
        sample = dict(resource_governor.last_snapshot)
    except Exception:
        sample = {}
    try:
        run = dict(parallel_image_runner.last_run_snapshot)
    except Exception:
        run = {}
    try:
        cumulative = dict(parallel_image_runner.cumulative_snapshot)
    except Exception:
        cumulative = {}
    try:
        active_child_count = len(parallel_image_runner.active_processes)
    except Exception:
        active_child_count = 0
    try:
        throttling = dict(resource_governor.cpu_throttling_snapshot())
    except Exception:
        throttling = {}

    def optional_number(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        converted = float(value)
        return converted if math.isfinite(converted) and converted >= 0 else None

    def nonnegative_int(value, default=0, maximum=None):
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return default
        return value if maximum is None else min(value, maximum)

    known_reasons = {
        "not_run",
        "resource_snapshot_unavailable",
        "serial_requested",
        "cpu_quota_below_parallel_threshold",
        "memory_below_parallel_threshold",
        "swap_above_parallel_threshold",
        "parallel_threshold_not_met",
        "parallel_batch_busy",
    }
    reason = run.get("reason")
    degrade_reason = reason if reason in known_reasons else None
    raw_admission_counts = cumulative.get("admission_tier_counts", {})
    if not isinstance(raw_admission_counts, Mapping):
        raw_admission_counts = {}
    raw_reason_counts = cumulative.get(
        "serial_fallback_reason_counts",
        {},
    )
    if not isinstance(raw_reason_counts, Mapping):
        raw_reason_counts = {}
    serial_reason_counts = {
        known_reason: nonnegative_int(raw_reason_counts.get(known_reason))
        for known_reason in sorted(known_reasons)
        if nonnegative_int(raw_reason_counts.get(known_reason)) > 0
    }
    status = run.get("status")
    if status not in {"not_run", "succeeded", "failed", "canceled"}:
        status = "unknown"

    worker_count = nonnegative_int(run.get("worker_count"), default=1, maximum=3)
    if worker_count < 1:
        worker_count = 1
    selected_tier = {
        1: "serial",
        2: "2_worker",
        3: "3_worker",
    }[worker_count]
    return {
        "resource_sample": {
            "available_mb": optional_number(sample.get("available_mb")),
            "swap_percent": optional_number(sample.get("swap_percent")),
            "cpu_quota_cores": optional_number(sample.get("cpu_quota_cores")),
        },
        "selected_tier": selected_tier,
        "worker_count": worker_count,
        "degrade_reason": degrade_reason,
        "status": status,
        "batch_duration_ms": optional_number(run.get("batch_duration_ms")) or 0.0,
        "worker_thread_count": nonnegative_int(
            run.get("worker_thread_count"),
            maximum=3,
        ),
        "child_peak_rss_bytes": nonnegative_int(
            run.get("child_peak_rss_bytes"),
            default=None,
        ),
        "cancellation_count": nonnegative_int(run.get("cancellation_count")),
        "active_child_count": nonnegative_int(active_child_count, maximum=1),
        "cumulative": {
            "admission_tier_counts": {
                "serial": nonnegative_int(
                    raw_admission_counts.get("serial")
                ),
                "2_worker": nonnegative_int(
                    raw_admission_counts.get("2_worker")
                ),
                "3_worker": nonnegative_int(
                    raw_admission_counts.get("3_worker")
                ),
            },
            "serial_fallback_reason_counts": serial_reason_counts,
            "batch_count": nonnegative_int(cumulative.get("batch_count")),
            "batch_duration_ms_total": (
                optional_number(cumulative.get("batch_duration_ms_total"))
                or 0.0
            ),
            "normalized_work_pixels_total": nonnegative_int(
                cumulative.get("normalized_work_pixels_total")
            ),
            "child_peak_rss_bytes": nonnegative_int(
                cumulative.get("child_peak_rss_bytes"),
                default=None,
            ),
            "cancellation_count": nonnegative_int(
                cumulative.get("cancellation_count")
            ),
        },
        "cpu_throttling": {
            "nr_periods": nonnegative_int(
                throttling.get("nr_periods"),
                default=None,
            ),
            "nr_throttled": nonnegative_int(
                throttling.get("nr_throttled"),
                default=None,
            ),
            "throttled_usec": nonnegative_int(
                throttling.get("throttled_usec"),
                default=None,
            ),
        },
    }
