"""A headshot or flag its host answers 404 for is remembered on disk for a day. Each Sports
Dashboard region is drawn by a fresh worker process, so a note kept only in memory asks for the
same missing file again on every render (980 requests in 48 hours on the device, 2026-11-19)."""

import pytest
import requests

from plugins.sports_dashboard import common, icon_marks
from plugins.sports_dashboard.sports_dashboard import SportsDashboard
from runtime.sports_asset_metrics import capture_asset_metrics

HEADSHOT = "https://a.espncdn.com/i/headshots/tennis/players/full/9250.png"
PHOTO = "https://wtafiles.blob.core.windows.net/images/headshots/330569.jpg"


def _http_error(url, status):
    response = requests.Response()
    response.status_code = status
    response.url = url
    return requests.HTTPError(f"{status} Client Error for url: {url}", response=response)


def _fresh_worker():
    """What a new worker process starts with: no decoded images and no failure notes in memory."""
    common.TEAM_LOGO_CACHE.clear()
    common.FLAG_IMAGE_CACHE.clear()
    icon_marks._flag_failures.clear()


@pytest.fixture(autouse=True)
def _isolated_memory():
    _fresh_worker()
    yield
    _fresh_worker()


def _answering(monkeypatch, error):
    asked = []

    def fetch(url, timeout):
        asked.append(url)
        raise error

    monkeypatch.setattr(SportsDashboard, "_fetch_remote_image_bytes", staticmethod(fetch))
    return asked


def test_a_headshot_the_host_does_not_have_is_asked_for_once_across_workers(monkeypatch, tmp_path):
    asked = _answering(monkeypatch, _http_error(HEADSHOT, 404))

    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None
    _fresh_worker()
    with capture_asset_metrics() as counters:
        assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None

    assert asked == [HEADSHOT]
    assert counters["negative_hits"] == 1 and counters["load_failures"] == 0


@pytest.mark.parametrize("error", [OSError("offline"), _http_error(HEADSHOT, 503), _http_error(HEADSHOT, 429)],
                         ids=["offline", "503", "429"])
def test_a_headshot_that_failed_for_a_passing_reason_is_asked_for_by_the_next_worker(monkeypatch, tmp_path, error):
    asked = _answering(monkeypatch, error)

    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None
    _fresh_worker()
    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None

    assert asked == [HEADSHOT, HEADSHOT]


def test_a_missing_headshot_is_asked_for_again_after_a_day(monkeypatch, tmp_path):
    asked = _answering(monkeypatch, _http_error(HEADSHOT, 404))
    real_now = common.time.time()

    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None
    _fresh_worker()
    monkeypatch.setattr(common.time, "time", lambda: real_now + common.TEAM_LOGO_MISSING_SECONDS - 60)
    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None
    assert asked == [HEADSHOT]

    _fresh_worker()
    monkeypatch.setattr(common.time, "time", lambda: real_now + common.TEAM_LOGO_MISSING_SECONDS + 60)
    assert SportsDashboard._load_team_logo(HEADSHOT, 40, cache_dir=tmp_path) is None
    assert asked == [HEADSHOT, HEADSHOT]


def test_a_photo_the_host_does_not_have_is_asked_for_once_across_workers(monkeypatch, tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_team_logo_disk_cache_dir", lambda: tmp_path)
    asked = _answering(monkeypatch, _http_error(PHOTO, 404))

    assert plugin._load_icon_flag(PHOTO, (60, 60)) is None
    _fresh_worker()
    assert plugin._load_icon_flag(PHOTO, (60, 60)) is None

    assert asked == [PHOTO]


def test_a_photo_that_failed_for_a_passing_reason_is_asked_for_by_the_next_worker(monkeypatch, tmp_path):
    plugin = SportsDashboard({"id": "sports_dashboard"})
    monkeypatch.setattr(plugin, "_team_logo_disk_cache_dir", lambda: tmp_path)
    asked = _answering(monkeypatch, _http_error(PHOTO, 429))

    assert plugin._load_icon_flag(PHOTO, (60, 60)) is None
    _fresh_worker()
    assert plugin._load_icon_flag(PHOTO, (60, 60)) is None

    assert asked == [PHOTO, PHOTO]
