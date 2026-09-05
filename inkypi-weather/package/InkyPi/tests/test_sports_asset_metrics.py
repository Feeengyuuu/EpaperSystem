"""Asset counters distinguish reuse from real download and corruption repair."""

from io import BytesIO

from PIL import Image

from runtime.sports_asset_metrics import capture_asset_metrics
from plugins.sports_dashboard import common
from plugins.sports_dashboard.sports_dashboard import SportsDashboard, TEAM_LOGO_CACHE


def test_team_logo_metrics_follow_disk_reuse_and_corruption_repair(monkeypatch, tmp_path):
    with Image.new('RGBA', (12, 12), (30, 50, 70, 220)) as source:
        output = BytesIO()
        source.save(output, format='PNG')
    data = output.getvalue()
    url = 'https://example.test/team.png'
    downloads = []

    def fetch(*_a, **_k):
        downloads.append(True)
        return data

    monkeypatch.setattr(SportsDashboard, '_fetch_remote_image_bytes', fetch)
    TEAM_LOGO_CACHE.clear()
    try:
        with capture_asset_metrics() as counters:
            first = SportsDashboard._load_team_logo(url, 10, cache_dir=tmp_path)
            assert first is not None
            expected_pixels = first.tobytes()
            assert SportsDashboard._load_team_logo(url, 10, cache_dir=tmp_path).tobytes() == expected_pixels
            TEAM_LOGO_CACHE.clear()
            assert SportsDashboard._load_team_logo(url, 10, cache_dir=tmp_path).tobytes() == expected_pixels
            TEAM_LOGO_CACHE.clear()
            next(tmp_path.glob('*.png')).write_bytes(b'corrupt')
            assert SportsDashboard._load_team_logo(url, 10, cache_dir=tmp_path).tobytes() == expected_pixels
        assert len(downloads) == 2
        assert dict(counters) == {
            'downloads': 2, 'downloaded_bytes': 2 * len(data),
            'memory_hits': 1, 'disk_hits': 1, 'invalid_disk_entries': 1,
        }
        assert url not in str(counters)
    finally:
        TEAM_LOGO_CACHE.clear()


def test_cached_logo_failure_is_not_reported_as_successful_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(SportsDashboard, '_fetch_remote_image_bytes', lambda *_a: b'invalid')
    TEAM_LOGO_CACHE.clear()
    try:
        with capture_asset_metrics() as counters:
            assert SportsDashboard._load_team_logo('https://example.test/bad.png', 10, cache_dir=tmp_path) is None
            assert SportsDashboard._load_team_logo('https://example.test/bad.png', 10, cache_dir=tmp_path) is None
        assert counters['load_failures'] == 1
        assert counters['negative_hits'] == 1
        assert counters['memory_hits'] == 0
    finally:
        TEAM_LOGO_CACHE.clear()


def test_expired_disk_entry_is_a_miss_not_corruption(monkeypatch, tmp_path):
    path = tmp_path / 'expired.png'
    path.write_bytes(b'entry present at initial stat')

    class ExpiredNamespace:
        def get_bytes(self, *_args, **_kwargs):
            return None

        def remove(self, *_args, **_kwargs):
            pass

    monkeypatch.setattr(common, 'cache_namespace_for_directory', lambda *_a: ExpiredNamespace())
    with capture_asset_metrics() as counters:
        assert SportsDashboard._read_team_logo_disk_cache(path) is None
    assert counters['invalid_disk_entries'] == 0
