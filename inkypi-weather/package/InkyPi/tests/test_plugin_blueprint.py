from src.blueprints.plugin import _display_instance_force_refresh


def test_display_instance_force_refresh_skips_sports_dashboard_force_mode():
    assert _display_instance_force_refresh("sports_dashboard", cache_refresh_busy=False) is False
    assert _display_instance_force_refresh("newspaper", cache_refresh_busy=False) is True
    assert _display_instance_force_refresh("newspaper", cache_refresh_busy=True) is False