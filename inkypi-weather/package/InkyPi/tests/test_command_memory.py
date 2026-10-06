from runtime.command_memory import CommandPeakMeter


def _set_hwm(proc, hwm_kb, rss_kb=50_000):
    (proc / "status").write_text(f"Name:\tpython\nVmHWM:\t{hwm_kb} kB\nVmRSS:\t{rss_kb} kB\n", encoding="ascii")


def _proc(tmp_path, hwm_kb):
    proc = tmp_path / "proc-self"
    proc.mkdir(exist_ok=True)
    _set_hwm(proc, hwm_kb)
    (proc / "clear_refs").write_text("", encoding="ascii")
    return proc


def test_start_folds_the_peak_into_the_lifetime_peak_and_resets_it(tmp_path):
    proc = _proc(tmp_path, hwm_kb=240 * 1024)
    meter = CommandPeakMeter(proc)

    meter.start()
    # The kernel now reports the peak since the reset.
    _set_hwm(proc, 130 * 1024)

    assert (proc / "clear_refs").read_text(encoding="ascii") == "5"
    assert meter.command_peak_mb() == 130
    assert meter.lifetime_peak_mb() == 240


def test_lifetime_peak_follows_a_command_that_exceeds_it(tmp_path):
    proc = _proc(tmp_path, hwm_kb=100 * 1024)
    meter = CommandPeakMeter(proc)
    meter.start()
    _set_hwm(proc, 180 * 1024)

    assert meter.command_peak_mb() == 180
    assert meter.lifetime_peak_mb() == 180


def test_no_command_peak_before_the_first_command(tmp_path):
    meter = CommandPeakMeter(_proc(tmp_path, hwm_kb=90 * 1024))

    assert meter.command_peak_mb() is None
    assert meter.lifetime_peak_mb() == 90


def test_unsupported_platform_reports_nothing(tmp_path):
    meter = CommandPeakMeter(tmp_path / "missing")

    meter.start()

    assert meter.command_peak_mb() is None
    assert meter.lifetime_peak_mb() is None


def test_reset_failure_disables_command_peaks_but_keeps_the_lifetime_peak(tmp_path):
    proc = _proc(tmp_path, hwm_kb=150 * 1024)
    (proc / "clear_refs").unlink()
    (proc / "clear_refs").mkdir()
    meter = CommandPeakMeter(proc)

    meter.start()
    meter.start()

    assert meter.command_peak_mb() is None
    assert meter.lifetime_peak_mb() == 150
