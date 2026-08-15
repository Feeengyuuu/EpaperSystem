import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from runtime.plugin_deferral import (  # noqa: E402
    MAX_PLUGIN_REFRESH_DEFERRAL_SECONDS,
    PluginRefreshDeferred,
)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("reason", "pixiv_bank\nforged_log"),
        ("phase", "a" * 65),
        ("reason", "session=super-secret-value"),
        ("phase", "BankAdmission"),
    ],
)
def test_plugin_deferral_rejects_untrusted_log_tokens(field, value):
    arguments = {
        "reason": "pixiv_bank_protected_capacity",
        "phase": "bank_admission",
        "minimum_seconds": 30 * 60,
    }
    arguments[field] = value

    with pytest.raises(ValueError, match=field):
        PluginRefreshDeferred(**arguments)


@pytest.mark.parametrize(
    "minimum_seconds",
    [0, float("inf"), MAX_PLUGIN_REFRESH_DEFERRAL_SECONDS + 1],
)
def test_plugin_deferral_rejects_unbounded_retry_delays(minimum_seconds):
    with pytest.raises(ValueError, match="minimum_seconds"):
        PluginRefreshDeferred(
            reason="pixiv_bank_protected_capacity",
            phase="bank_admission",
            minimum_seconds=minimum_seconds,
        )
