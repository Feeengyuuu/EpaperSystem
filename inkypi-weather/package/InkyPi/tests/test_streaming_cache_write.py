import json
import tracemalloc

import pytest

from plugins.sports_dashboard.cache_io import write_json_file
from utils.atomic_file import AtomicWriteError


def test_large_sports_cache_write_has_bounded_encoding_memory(tmp_path):
    payload = {"events": [{"id": str(i), "note": "中文" * 100} for i in range(6000)]}
    target = tmp_path / "scoreboard.json"
    tracemalloc.start()
    try:
        write_json_file(target, payload)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert json.loads(target.read_text(encoding="utf-8")) == payload
    assert peak < 2 * 1024 * 1024, f"cache encoder allocated {peak} bytes"


def test_failed_streaming_cache_write_preserves_last_good_target(tmp_path):
    target = tmp_path / "scoreboard.json"
    target.write_text('{"previous":true}', encoding="utf-8")
    with pytest.raises((AtomicWriteError, ValueError, TypeError)):
        write_json_file(target, {"events": [{"id": "ok"}, {"score": float("nan")}]})
    assert json.loads(target.read_text()) == {"previous": True}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["scoreboard.json"]
