"""The service bounds glibc's malloc arenas and returns large buffers to the system at once."""

from types import SimpleNamespace

from runtime import malloc_tuning


class FakeLibc:
    def __init__(self, result=1):
        self.calls = []
        self.result = result

    def mallopt(self, param, value):
        self.calls.append((param, value))
        return self.result


def test_linux_gets_two_arenas_and_a_fixed_mmap_threshold():
    libc = FakeLibc()

    result = malloc_tuning.limit_malloc_arenas(platform="linux", environ={}, load_libc=lambda: libc)

    assert libc.calls == [(malloc_tuning.M_ARENA_MAX, 2), (malloc_tuning.M_MMAP_THRESHOLD, 256 * 1024)]
    assert result == {"arena_max": 2, "mmap_threshold": 256 * 1024}


def test_values_set_by_the_operator_in_the_environment_are_left_alone():
    libc = FakeLibc()

    result = malloc_tuning.limit_malloc_arenas(
        platform="linux", environ={"MALLOC_ARENA_MAX": "4", "MALLOC_MMAP_THRESHOLD_": "131072"},
        load_libc=lambda: libc)

    assert libc.calls == []
    assert result == {}


def test_other_systems_and_a_missing_mallopt_change_nothing():
    libc = FakeLibc()
    assert malloc_tuning.limit_malloc_arenas(platform="win32", environ={}, load_libc=lambda: libc) == {}
    assert libc.calls == []

    def unavailable():
        raise OSError("no libc")

    assert malloc_tuning.limit_malloc_arenas(platform="linux", environ={}, load_libc=unavailable) == {}
    assert malloc_tuning.limit_malloc_arenas(platform="linux", environ={},
                                             load_libc=lambda: SimpleNamespace()) == {}


def test_a_refused_setting_is_not_reported_as_applied():
    assert malloc_tuning.limit_malloc_arenas(platform="linux", environ={},
                                             load_libc=lambda: FakeLibc(result=0)) == {}


def test_main_limits_the_arenas_before_any_service_thread_starts(monkeypatch):
    import inkypi

    events = []
    monkeypatch.setattr(inkypi, "limit_malloc_arenas", lambda: events.append("malloc") or {"arena_max": 2})
    monkeypatch.setattr(inkypi, "_configure_process_logging", lambda: events.append("logging"))
    monkeypatch.setattr(inkypi, "disable_wifi_powersave", lambda: events.append("wifi"))
    monkeypatch.setattr(inkypi, "start_wifi_reconnect_watchdog", lambda: events.append("watchdog"))
    monkeypatch.setattr(inkypi, "build_application", lambda **_kwargs: events.append("build") or "app")
    monkeypatch.setattr(inkypi, "run", lambda app, **_kwargs: events.append("run") or 0)

    assert inkypi.main([]) == 0
    assert events == ["malloc", "logging", "wifi", "watchdog", "build", "run"]
