"""Per-command peak RSS on Linux.

The kernel's VmHWM only ever rises, so the journal could not tell which command set
a peak (2026-10-06 memory ranking blamed whichever plugin ran next). Writing "5" to
/proc/self/clear_refs resets VmHWM to the current RSS; resetting as each command
starts makes VmHWM at its end that command's own peak. The lifetime peak is carried
across resets so the existing process_hwm_mb log field keeps its meaning.
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
PROC_SELF = Path("/proc/self")


class CommandPeakMeter:
    def __init__(self, proc_dir=PROC_SELF):
        self._status = Path(proc_dir) / "status"
        self._clear_refs = Path(proc_dir) / "clear_refs"
        self._lifetime_kb = 0
        self._resettable = self._status.is_file()
        self._measuring = False

    def start(self) -> None:
        """Fold the current peak into the lifetime peak, then reset it for the next command."""

        if not self._resettable:
            return
        current = self._read_hwm_kb()
        if current is None:
            self._resettable = False
            return
        self._lifetime_kb = max(self._lifetime_kb, current)
        try:
            with self._clear_refs.open("w", encoding="ascii") as handle:
                handle.write("5")
        except OSError:
            logger.info("Per-command peak memory is unavailable: VmHWM cannot be reset.")
            self._resettable = False
            self._measuring = False
            return
        self._measuring = True

    def command_peak_mb(self) -> float | None:
        if not self._measuring:
            return None
        current = self._read_hwm_kb()
        return None if current is None else current / 1024

    def lifetime_peak_mb(self) -> float | None:
        peak = max(self._lifetime_kb, self._read_hwm_kb() or 0)
        return peak / 1024 if peak else None

    def _read_hwm_kb(self) -> int | None:
        try:
            with self._status.open(encoding="ascii") as handle:
                for line in handle:
                    if line.startswith("VmHWM:"):
                        return int(line.split()[1])
        except (OSError, ValueError, IndexError):
            return None
        return None
