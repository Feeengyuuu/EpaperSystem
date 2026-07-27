"""Short-lived Sports Dashboard region renderer.

This module intentionally keeps its import surface small. The spawned worker
raises its own OOM preference before importing the large Sports Dashboard
module so earlyoom can sacrifice the worker without taking down the InkyPi
service process.
"""

from __future__ import annotations

from datetime import datetime
from io import BytesIO
import os


WORKER_OOM_SCORE_ADJ = 800
PANEL_PROVENANCE_ORDER = ("football", "lower", "esports")


def _is_posix_platform():
    return os.name == "posix"


def _prefer_worker_as_oom_victim(
    score_path="/proc/self/oom_score_adj",
):
    if not _is_posix_platform():
        return False
    try:
        with open(score_path, "w", encoding="ascii") as handle:
            handle.write(str(WORKER_OOM_SCORE_ADJ))
        with open(score_path, "r", encoding="ascii") as handle:
            applied_value = int(handle.read().strip())
        return applied_value >= WORKER_OOM_SCORE_ADJ
    except (OSError, TypeError, ValueError):
        return False


def _require_worker_oom_preference():
    adjusted = _prefer_worker_as_oom_victim()
    if _is_posix_platform() and not adjusted:
        raise RuntimeError(
            "isolated Sports Dashboard worker could not establish OOM isolation"
        )
    return WORKER_OOM_SCORE_ADJ if adjusted else None


class _WorkerDeviceConfig:
    def __init__(self, values, env_file=None):
        self._values = dict(values or {})
        self._env_values = dict(os.environ)
        if env_file:
            try:
                from dotenv import dotenv_values

                self._env_values.update(
                    {
                        key: value
                        for key, value in dotenv_values(env_file).items()
                        if value is not None
                    }
                )
            except OSError:
                pass

    def get_config(self, key, default=None):
        return self._values.get(key, default)

    def get_resolution(self):
        resolution = self._values.get("resolution") or (800, 480)
        return int(resolution[0]), int(resolution[1])

    def load_env_key(self, key):
        try:
            from secret_schema import SecretSchema

            candidates = SecretSchema.load().resolve_names(key)
        except (KeyError, OSError, ValueError):
            candidates = (key,)
        for candidate in candidates:
            value = self._env_values.get(candidate)
            if value:
                return value
        return ""


def _ordered_panel_provenances(values, region, region_provenance):
    panel_provenance_values = dict(values or {})
    panel_provenance_values[region] = region_provenance.value
    return [
        region_provenance.__class__(panel_provenance_values[panel_region])
        for panel_region in PANEL_PROVENANCE_ORDER
    ]


def _ewc_event_identity(event):
    if not isinstance(event, dict):
        return None
    values = []
    for key in (
        "event_id",
        "slug",
        "game",
        "start",
        "team_a",
        "team_b",
        "score_a",
        "score_b",
        "status",
        "stage",
    ):
        value = event.get(key)
        if isinstance(value, datetime):
            values.append(("datetime", value.isoformat()))
        elif value is None:
            values.append(None)
        else:
            values.append((type(value).__name__, str(value)))
    return tuple(values)


def _ewc_card_selection_identity(card):
    selected = (card or {}).get("selected") or {}
    values = []
    for key in ("main", "main_match", "live", "upcoming", "recent"):
        value = selected.get(key)
        if isinstance(value, list):
            values.append(tuple(_ewc_event_identity(item) for item in value))
        else:
            values.append(_ewc_event_identity(value))
    return tuple(values)


def prefetch_ewc_detail_task(payload, cancel_event):
    """Refresh one bounded EWC detail page without loading other providers."""

    worker_oom_score_adj = _require_worker_oom_preference()
    if cancel_event.is_set():
        from runtime.refresh_contracts import TaskCancelled

        raise TaskCancelled("isolated Sports Dashboard EWC prefetch was canceled")

    from plugins.sports_dashboard.sports_dashboard import SportsDashboard
    from runtime.refresh_contracts import TaskCancelled

    device_config = _WorkerDeviceConfig(
        payload.get("device_config"),
        payload.get("env_file"),
    )
    plugin = SportsDashboard({"id": "sports_dashboard"})
    settings = dict(payload.get("settings") or {})
    settings["_inkypi_ewc_require_cache_publish"] = True
    now_value = payload.get("now")
    now = datetime.fromisoformat(now_value) if now_value else None
    timezone_info = plugin._timezone(settings, device_config)
    if now is None:
        now = datetime.now(timezone_info)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone_info)
    else:
        now = now.astimezone(timezone_info)
    prefetch_card = None
    degraded_reason = ""
    try:
        prefetch_card = plugin._load_ewc_sidebar_card(
            settings,
            timezone_info,
            now,
        )
    except OSError:
        # Cache publication is the hand-off boundary between this short-lived
        # parser and the later cache-only panel worker. Preserve Dashboard
        # availability with the last durable cache, but report the degraded
        # hand-off explicitly instead of claiming a successful prefetch.
        degraded_reason = "cache_publish_failed"
    cache_only_settings = dict(settings)
    cache_only_settings.pop("_inkypi_ewc_require_cache_publish", None)
    cache_only_settings["_inkypi_ewc_cache_only"] = True
    cached_card = plugin._load_ewc_sidebar_card(
        cache_only_settings,
        timezone_info,
        now,
    )
    handoff_matches = (
        not degraded_reason
        and _ewc_card_selection_identity(prefetch_card)
        == _ewc_card_selection_identity(cached_card)
    )
    if not degraded_reason and not handoff_matches:
        degraded_reason = "cache_attestation_mismatch"
    if cancel_event.is_set():
        raise TaskCancelled("isolated Sports Dashboard EWC prefetch was canceled")
    selected = (cached_card or {}).get("selected") or {}
    return {
        "region": "ewc_prefetch",
        "source_state": (cached_card or {}).get("source_state") or "",
        "prefetch_source_state": (
            (prefetch_card or {}).get("source_state") or ""
        ),
        "has_detail": bool(selected.get("main_match")),
        "cache_handoff_verified": True,
        "prefetch_handoff_matches": handoff_matches,
        "degraded_reason": degraded_reason,
        "worker_oom_score_adj": worker_oom_score_adj,
        "worker_pid": os.getpid(),
    }


def render_sports_region_task(payload, cancel_event):
    """Render exactly one provider region and return bounded PNG bytes."""

    worker_oom_score_adj = _require_worker_oom_preference()
    if cancel_event.is_set():
        from runtime.refresh_contracts import TaskCancelled

        raise TaskCancelled("isolated Sports Dashboard region was canceled")

    # Import the heavyweight renderer only after making this child the preferred
    # earlyoom victim.
    from plugins.base_plugin.render_provenance import read_source_provenance
    from plugins.sports_dashboard.sports_dashboard import SportsDashboard
    from runtime.refresh_contracts import TaskCancelled
    from utils.safe_image import ImageLimits, safe_open_image

    base_png = payload.get("base_png")
    base_image = None
    if base_png:
        base_image = safe_open_image(
            base_png,
            limits=ImageLimits(
                max_bytes=8 * 1024 * 1024,
                max_width=2048,
                max_height=2048,
                max_pixels=4_000_000,
                allowed_formats=frozenset({"PNG"}),
            ),
        ).convert("RGB")

    device_config = _WorkerDeviceConfig(
        payload.get("device_config"),
        payload.get("env_file"),
    )
    plugin = SportsDashboard({"id": "sports_dashboard"})
    plugin._isolated_region_cache_identity = str(
        payload.get("cache_identity") or ""
    )
    now_value = payload.get("now")
    now = datetime.fromisoformat(now_value) if now_value else None
    image, region_provenance = plugin.render_isolated_region(
        payload.get("settings") or {},
        device_config,
        region=payload["region"],
        base_image=base_image,
        now=now,
    )
    if cancel_event.is_set():
        raise TaskCancelled("isolated Sports Dashboard region was canceled")

    response = {
        "region": payload["region"],
        "region_provenance": region_provenance.value,
        "worker_oom_score_adj": worker_oom_score_adj,
        "worker_pid": os.getpid(),
    }
    if payload.get("finalize"):
        panel_provenances = _ordered_panel_provenances(
            payload.get("panel_provenances"),
            payload["region"],
            region_provenance,
        )
        primary_live_override = (
            plugin._worldcup_release_one_shot_window_active(now)
            and panel_provenances
            and panel_provenances[0].value == "live"
        )
        image = plugin._attest_sports_dashboard_image(
            image,
            *panel_provenances,
            force_refresh=plugin._force_refresh_requested(
                payload.get("settings") or {}
            ),
            primary_live_override=primary_live_override,
        )
        composite = read_source_provenance(image)
        response.update(
            {
                "composite_provenance": composite.value,
                "skip_cache": bool(image.info.get("inkypi_skip_cache")),
            }
        )

    output = BytesIO()
    image.save(output, format="PNG", optimize=True)
    response["image_png"] = output.getvalue()
    response["theme_mode"] = image.info.get("inkypi_theme_mode")
    return response
