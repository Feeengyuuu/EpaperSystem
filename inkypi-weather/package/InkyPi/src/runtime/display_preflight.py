"""Metadata-only eligibility for provider and presentation display preflight."""

from datetime import datetime

from plugins.plugin_registry import (
    plugin_refreshes_data_before_display, plugin_supports_presentation_refresh,
)
from plugins.plugin_settings import resolve_refresh_on_display_for_config
from runtime.refresh_contracts import thaw_payload
from runtime.refresh_planning import align_datetime_tz
from runtime.runtime_state import InstanceRuntimeState


def filter_display_preflight_candidates(
    active, candidates, runtime_instances, now, *, plugin_config_for,
    presentation_enabled, presentation_retry_delayed,
):
    """A failed fresh display waits; ordinary cached pages remain eligible."""
    instances = {instance.instance_uuid: instance for instance in active.plugins}
    eligible = {}
    for identity, candidate in candidates.items():
        instance = instances.get(identity)
        if instance is None:
            eligible[identity] = candidate
            continue
        config = plugin_config_for(instance.plugin_id)
        state = runtime_instances.get(identity, InstanceRuntimeState())
        if plugin_refreshes_data_before_display(config):
            try:
                retry = datetime.fromisoformat(state.data.next_retry_at)
            except (TypeError, ValueError):
                retry = None
            if retry is not None and align_datetime_tz(retry, now) > now:
                continue
        if presentation_enabled(config) and plugin_supports_presentation_refresh(config):
            try:
                requested = resolve_refresh_on_display_for_config(thaw_payload(instance.settings), config)
            except Exception:
                requested = False
            if requested and presentation_retry_delayed(state, now):
                continue
        eligible[identity] = candidate
    return eligible
