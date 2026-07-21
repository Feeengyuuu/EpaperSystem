from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from utils.render_policy import prefer_native_renderer


def test_prefer_native_renderer_honors_explicit_setting(monkeypatch):
    monkeypatch.setenv("INKYPI_NATIVE_RENDERER_FIRST", "1")

    assert prefer_native_renderer({"preferPilFallback": "false"}) is False
    assert prefer_native_renderer({"preferPilFallback": "true"}) is True


def test_prefer_native_renderer_honors_feature_and_global_env(monkeypatch):
    monkeypatch.delenv("INKYPI_NATIVE_RENDERER_FIRST", raising=False)
    monkeypatch.setenv("INKYPI_WEATHER_PIL_FIRST", "1")

    assert prefer_native_renderer({}, feature_env="INKYPI_WEATHER_PIL_FIRST") is True

    monkeypatch.setenv("INKYPI_WEATHER_PIL_FIRST", "0")
    monkeypatch.setenv("INKYPI_NATIVE_RENDERER_FIRST", "1")
    assert prefer_native_renderer({}, feature_env="INKYPI_WEATHER_PIL_FIRST") is False


def test_prefer_native_renderer_detects_sub_gib_runtime(monkeypatch, tmp_path):
    monkeypatch.delenv("INKYPI_NATIVE_RENDERER_FIRST", raising=False)
    meminfo = tmp_path / "meminfo"
    meminfo.write_text("MemTotal:         426000 kB\n", encoding="ascii")

    assert prefer_native_renderer({}, meminfo_path=meminfo) is True

    meminfo.write_text("MemTotal:        4194304 kB\n", encoding="ascii")
    assert prefer_native_renderer({}, meminfo_path=meminfo) is False
