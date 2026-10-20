"""A no-change presentation must not keep re-reserving a member rotation cannot show.

2026-10-19 08:56-12:00 PDT: Pixiv's bank went cold at the JST day change while its
only cache was from the night theme.  Each cold preparation returned no change,
display admission (exact current-theme cache or prepared request) skipped it, and
the post-display reservation picked it again after every display.  The reservation
kept the reserved-round background guard closed for the last 120 s of every round
and starved the other presentation members of preparation.
"""

from datetime import timedelta

from PIL import Image

import pytest

from tests.test_refresh_task import (
    LastGoodCacheState,
    NoChangePresentationPlugin,
    PRESENTATION_NOW,
    PresentationBankPlugin,
    RefreshIntent,
    RefreshLane,
    ResourceSample,
    _make_presentation_task,
    _queue_and_process,
    _write_runtime_theme_cache,
    refresh_task_module,
)


def _record_data_success(task, instance, *, theme_mode, at):
    task.runtime_state.record_success(
        instance.instance_uuid,
        at.isoformat(),
        lane=RefreshLane.DATA,
        last_good_cache=LastGoodCacheState(
            theme_mode=theme_mode,
            structural_generation=instance.structural_generation,
            settings_revision=instance.settings_revision,
            promoted_at=at.isoformat(),
        ),
    )


def _reserved_no_change_case(name, monkeypatch, *, cold_cache_theme, plugin=None):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        name,
        plugin_count=3,
        provider_free=True,
        supports_theme=True,
    )
    cold, first_other, second_other = [plugin.snapshot() for plugin in playlist.plugins]
    device_config.config["plugin_cycle_interval_seconds"] = 300
    earlier = PRESENTATION_NOW - timedelta(minutes=10)
    _write_runtime_theme_cache(task, cold, cold_cache_theme, Image.new("RGB", (32, 16), "black"))
    _record_data_success(task, cold, theme_mode=cold_cache_theme, at=earlier)
    for instance in (first_other, second_other):
        _write_runtime_theme_cache(task, instance, "day", Image.new("RGB", (32, 16), "black"))
        _record_data_success(task, instance, theme_mode="day", at=earlier)
    keys = [cold.instance_uuid, first_other.instance_uuid, second_other.instance_uuid]
    playlist.plugin_rotation_pool = list(keys)
    playlist.plugin_rotation_queue = list(keys)
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    plugin = plugin or PresentationBankPlugin(changed=False)
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )

    assert task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "origin-display-commit",
        PRESENTATION_NOW.isoformat(),
    ) is True
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is True
    command = task._select_independent_refresh_command(PRESENTATION_NOW)
    assert command is not None
    assert command.intent is RefreshIntent.PRESENTATION_REFRESH
    assert command.instance_uuid == cold.instance_uuid
    assert command.payload["automatic_rotation"] is True
    _queue_and_process(task, command)
    assert [event[0] for event in plugin.events][0] == "mode"
    state = task.runtime_state.snapshot().instances[cold.instance_uuid]
    assert state.presentation_request is None
    return task, playlist, cold, first_other, second_other


def _display_and_request_next(task, playlist, displayed):
    playlist._plugin_rotation_reserved_key = displayed.instance_uuid
    assert task.device_config.playlist_manager.acknowledge_rotation_display(
        displayed.instance_uuid,
        expected_playlist_name=playlist.name,
    ) is not None
    return task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        f"display-{displayed.instance_uuid}",
        PRESENTATION_NOW.isoformat(),
        displayed_instance_uuid=displayed.instance_uuid,
    )


@pytest.mark.parametrize(
    "plugin_factory",
    [lambda: PresentationBankPlugin(changed=False), NoChangePresentationPlugin],
    ids=["prepared-bank-unchanged", "no-change-mode"],
)
def test_undisplayable_no_change_member_yields_its_reservation(monkeypatch, plugin_factory):
    task, playlist, cold, first_other, second_other = _reserved_no_change_case(
        "no-change-undisplayable-yields-reservation",
        monkeypatch,
        cold_cache_theme="night",
        plugin=plugin_factory(),
    )

    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is False
    assert playlist.plugin_rotation_queue == [
        first_other.instance_uuid,
        second_other.instance_uuid,
        cold.instance_uuid,
    ]
    # Without a reservation the reserved-round guard no longer closes the tail.
    assert not any(
        task.device_config.playlist_manager.validate_rotation_reservation(
            instance.instance_uuid,
            expected_playlist_name=playlist.name,
        )
        for instance in (cold, first_other, second_other)
    )


def test_undisplayable_no_change_member_is_not_rereserved_until_its_data_succeeds(
    monkeypatch,
):
    task, playlist, cold, first_other, second_other = _reserved_no_change_case(
        "no-change-undisplayable-not-rereserved",
        monkeypatch,
        cold_cache_theme="night",
    )

    assert _display_and_request_next(task, playlist, first_other) is True
    assert playlist.is_rotation_reservation_current(second_other.instance_uuid) is True

    # Only the cold member remains: it must not be reserved again after every display.
    assert _display_and_request_next(task, playlist, second_other) is False
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is False
    assert task.runtime_state.snapshot().instances[
        cold.instance_uuid
    ].presentation_request is None

    # A later data success can warm the bank, so the member is eligible again
    # even while its only cache is still the night one.
    _record_data_success(
        task,
        cold,
        theme_mode="night",
        at=PRESENTATION_NOW + timedelta(minutes=1),
    )
    assert task._request_next_presentation_after_display(
        PRESENTATION_NOW + timedelta(minutes=2),
        "after-data-success",
        (PRESENTATION_NOW + timedelta(minutes=2)).isoformat(),
        displayed_instance_uuid=second_other.instance_uuid,
    ) is True
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is True


def test_displayable_no_change_member_keeps_its_reservation_for_the_cached_display(
    monkeypatch,
):
    task, playlist, cold, _first_other, _second_other = _reserved_no_change_case(
        "no-change-displayable-keeps-reservation",
        monkeypatch,
        cold_cache_theme="day",
    )

    # Rotation can still show the exact cache, as on 2026-10-18 09:29.
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is True
    assert playlist.plugin_rotation_queue[0] == cold.instance_uuid


def test_yielded_member_returns_once_it_has_an_exact_current_theme_cache(monkeypatch):
    task, playlist, cold, first_other, second_other = _reserved_no_change_case(
        "no-change-yield-cleared-by-exact-cache",
        monkeypatch,
        cold_cache_theme="night",
    )
    # A media theme catch-up (or any day render) makes the member displayable.
    _write_runtime_theme_cache(task, cold, "day", Image.new("RGB", (32, 16), "white"))

    assert _display_and_request_next(task, playlist, first_other) is True
    assert _display_and_request_next(task, playlist, second_other) is True
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is True


def test_yield_releases_the_reservation_when_the_round_has_no_alternative(monkeypatch):
    task, device_config, _clock, playlist, _display = _make_presentation_task(
        "no-change-yield-releases-last-member",
        plugin_count=1,
        provider_free=True,
        supports_theme=True,
    )
    cold = playlist.plugins[0].snapshot()
    device_config.config["plugin_cycle_interval_seconds"] = 300
    _write_runtime_theme_cache(task, cold, "night", Image.new("RGB", (32, 16), "black"))
    _record_data_success(task, cold, theme_mode="night", at=PRESENTATION_NOW - timedelta(minutes=10))
    playlist.plugin_rotation_pool = [cold.instance_uuid]
    playlist.plugin_rotation_queue = [cold.instance_uuid]
    playlist.plugin_rotation_recent_history = []
    playlist._plugin_rotation_reserved_key = None
    plugin = PresentationBankPlugin(changed=False)
    monkeypatch.setattr(refresh_task_module, "get_plugin_instance", lambda _config: plugin)
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)
    monkeypatch.setattr(
        task,
        "_resource_sample",
        lambda: ResourceSample(available_mb=512, swap_percent=0),
    )
    assert task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "origin-display-commit",
        PRESENTATION_NOW.isoformat(),
    ) is True

    _queue_and_process(task, task._select_independent_refresh_command(PRESENTATION_NOW))

    # The member stays in its round; only the reservation (and its guard) ends.
    assert playlist.is_rotation_reservation_current(cold.instance_uuid) is False
    assert playlist.plugin_rotation_queue == [cold.instance_uuid]


def test_member_without_any_displayable_cache_is_not_reserved_after_display(monkeypatch):
    task, _device_config, _clock, playlist, _display = _make_presentation_task(
        "presentation-reservation-needs-a-cache",
        plugin_count=2,
        provider_free=True,
        supports_theme=True,
    )
    displayed, uncached = [plugin.snapshot() for plugin in playlist.plugins]
    keys = [displayed.instance_uuid, uncached.instance_uuid]
    playlist.plugin_rotation_pool = list(keys)
    playlist.plugin_rotation_queue = list(keys)
    playlist.plugin_rotation_recent_history = []
    monkeypatch.setattr(task, "_get_current_datetime", lambda: PRESENTATION_NOW)

    # No cache: the presentation can never become due and rotation cannot show
    # it, so a reservation would only hold the pre-display background guard.
    assert _display_and_request_next(task, playlist, displayed) is False
    assert playlist.is_rotation_reservation_current(uncached.instance_uuid) is False
    assert uncached.instance_uuid not in task.runtime_state.snapshot().instances

    _write_runtime_theme_cache(task, uncached, "night", Image.new("RGB", (32, 16), "black"))
    _record_data_success(task, uncached, theme_mode="night", at=PRESENTATION_NOW)
    assert task._request_next_presentation_after_display(
        PRESENTATION_NOW,
        "after-first-cache",
        PRESENTATION_NOW.isoformat(),
        displayed_instance_uuid=displayed.instance_uuid,
    ) is True
    assert playlist.is_rotation_reservation_current(uncached.instance_uuid) is True
