"""Small per-render counters; no URL, filesystem path or persistent writes."""

from collections import Counter
from contextlib import contextmanager
from contextvars import ContextVar


ASSET_METRIC_KEYS = (
    "memory_hits", "negative_hits", "disk_hits", "downloads", "downloaded_bytes",
    "invalid_disk_entries", "load_failures", "local_decodes",
)
_ACTIVE_METRICS = ContextVar("sports_asset_metrics", default=None)


@contextmanager
def capture_asset_metrics():
    """Collect one isolated render's asset loads without sharing other work."""
    metrics = Counter()
    token = _ACTIVE_METRICS.set(metrics)
    try:
        yield metrics
    finally:
        _ACTIVE_METRICS.reset(token)


def record_asset_metric(key, value=1):
    """Count a known event only while a render capture is active."""
    metrics = _ACTIVE_METRICS.get()
    if metrics is not None and key in ASSET_METRIC_KEYS and type(value) is int and value >= 0:
        metrics[key] += value


def asset_metric_summary(metrics):
    """Format only bounded integer counters returned by a region worker."""
    if not isinstance(metrics, dict):
        return "unavailable"
    return " | ".join(
        f"{key}: {metrics.get(key, 0)}"
        for key in ASSET_METRIC_KEYS
        if type(metrics.get(key, 0)) is int and 0 <= metrics.get(key, 0) <= 1_000_000_000
    )
