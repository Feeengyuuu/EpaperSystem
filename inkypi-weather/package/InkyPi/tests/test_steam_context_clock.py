"""Context provenance must represent the same instant outside the UTC timezone."""
from datetime import datetime, timedelta, timezone
import json

import pytest


@pytest.mark.parametrize("combined", [False, True])
def test_steam_context_stays_fresh_with_a_local_wall_clock(tmp_path, monkeypatch, combined):
    import plugins.steam_charts.steam_charts as module
    from plugins.context_cache import read_contexts

    instant = datetime(2026, 9, 27, 21, 38, 35, tzinfo=timezone.utc)
    local_zone = timezone(timedelta(hours=-7))

    class Clock:
        @staticmethod
        def now(tz=None):
            if tz is None:
                return instant.astimezone(local_zone).replace(tzinfo=None)
            return instant.astimezone(tz)

    monkeypatch.setattr(module, "datetime", Clock)
    monkeypatch.setenv("INKYPI_CONTEXT_CACHE_DIR", str(tmp_path))
    plugin = module.SteamCharts({"id": "steam_charts"})
    writer = plugin._write_combined_context if combined else plugin._write_charts_context
    writer({"label": "Test"}, [], "14:38")

    saved = json.loads((tmp_path / "steam_charts.json").read_text(encoding="utf-8"))
    assert datetime.fromisoformat(saved["generated_at"]) == instant
    contexts = read_contexts(["steam_charts"], now=instant + timedelta(minutes=1))
    assert len(contexts) == 1
    assert contexts[0]["age_seconds"] == 60
    assert contexts[0]["stale"] is False
