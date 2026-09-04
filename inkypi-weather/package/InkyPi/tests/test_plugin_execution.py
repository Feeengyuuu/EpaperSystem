"""Explicit execution capabilities and restoration at the legacy adapter boundary."""

from threading import Event

import pytest

from runtime.long_task_executor import current_instance_identity, current_task_context, InstanceIdentity
from runtime.plugin_execution import PluginExecutionContext
from runtime.refresh_contracts import TaskContext, TaskCancelled


def execution(identity="a"):
    return PluginExecutionContext(
        task=TaskContext(cancel_event=Event(), deadline_monotonic=float("inf")),
        identity=InstanceIdentity(identity, 1, 1),
    )


def test_explicit_context_binds_legacy_capabilities_and_restores_after_failure():
    outer, inner = execution("a"), execution("b")
    with outer.activate():
        assert current_task_context() is outer.task
        with pytest.raises(RuntimeError):
            with inner.activate():
                assert current_instance_identity() == inner.identity
                raise RuntimeError("render failed")
        assert current_instance_identity() == outer.identity
    assert current_task_context() is None
    assert current_instance_identity() is None


def test_cancelled_execution_does_not_enter_plugin_code():
    context = execution()
    context.task.cancel_event.set()
    called = []
    with pytest.raises(TaskCancelled):
        context.run(lambda: called.append(True))
    assert not called


def test_result_is_rejected_if_instance_revision_changes_during_execution():
    from dataclasses import replace

    current = [True]
    context = replace(execution(), identity_validator=lambda identity: current[0])
    with pytest.raises(TaskCancelled):
        context.run(lambda: current.__setitem__(0, False))


def test_base_plugin_explicit_render_contract_bridges_existing_plugins():
    from plugins.base_plugin.base_plugin import BasePlugin

    class ExistingPlugin(BasePlugin):
        def __init__(self):
            pass

        def render_themed_image(self, settings, device_config, **options):
            assert current_task_context() is context.task
            return (settings, device_config, options)

    context = execution()
    result = ExistingPlugin().render_with_context(
        {"color": "black"}, "device", execution_context=context, theme_render_only=True,
    )
    assert result == ({"color": "black"}, "device", {"theme_render_only": True})
