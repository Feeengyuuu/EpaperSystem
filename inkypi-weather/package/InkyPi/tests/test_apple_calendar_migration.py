import copy
import hashlib
import json

import pytest

import apple_calendar_migration as migration
from config import Config
from config_store import ConfigConflictError
from model import PlaylistManager

UUID = '5c5dc169-7c88-4e43-9396-563040442983'


def payload():
    document = {
        'schema_version': 1, 'config_revision': 11, 'resolution': [800, 480],
        'runtime_migrations': {'existing_v1': True}, 'custom_global': {'keep': [1, 2]},
        'playlist_config': {'active_playlist': 'DailyDoseOfDay', 'playlists': [{
            'name': 'DailyDoseOfDay', 'start_time': '00:00', 'end_time': '24:00',
            'plugins': [{
                'plugin_id': 'simple_calendar', 'name': 'Date', 'instance_uuid': UUID,
                'structural_generation': 1, 'settings_revision': 2, 'refresh': {'interval': 3600},
                'plugin_settings': {'showGameEvents': 'true', 'gameEventSeries[]': ['state_of_play'],
                                    'personalCalendarURLs[]': ['https://example.invalid/private.ics'],
                                    'holidayPreset': 'us_cn', 'customDate': ''},
            }, {
                'plugin_id': 'clock', 'name': 'Other', 'instance_uuid': '9a73b375-c375-4df7-ab85-b78c5dcb5127',
                'structural_generation': 1, 'settings_revision': 1,
                'refresh': {'interval': 600}, 'plugin_settings': {'other': 'keep'},
            }],
        }]},
    }
    document['playlist_config'] = PlaylistManager.from_dict(document['playlist_config']).to_dict()
    return document


def configure(monkeypatch, tmp_path, document, *, bind=True):
    if bind:
        monkeypatch.setattr(migration, 'TARGET_UUID_HASH', hashlib.sha256(UUID.encode()).hexdigest()[:16])
    path = tmp_path / 'device.json'
    path.write_text(json.dumps(document), encoding='utf-8')
    monkeypatch.setattr(Config, 'config_file', str(path))
    return path


def test_startup_enables_only_saved_apple_calendar_and_is_restart_safe(monkeypatch, tmp_path):
    original = payload()
    path = configure(monkeypatch, tmp_path, original)
    config = Config()
    saved = json.loads(path.read_text())
    expected = copy.deepcopy(original)
    target = expected['playlist_config']['playlists'][0]['plugins'][0]
    target['plugin_settings']['showAppleEvents'] = 'true'
    target['settings_revision'] = 3
    expected['runtime_migrations'][migration.MIGRATION_ID] = True
    expected['config_revision'] += 1
    assert saved == expected
    assert config.get_playlist_manager().find_plugin('simple_calendar', 'Date').settings == target['plugin_settings']
    first_bytes = path.read_bytes()
    Config()
    assert path.read_bytes() == first_bytes


@pytest.mark.parametrize('value', [False, 'false', 'off', 0, None, 'true', ''])
def test_explicit_apple_setting_is_never_overridden(monkeypatch, tmp_path, value):
    original = payload()
    original['playlist_config']['playlists'][0]['plugins'][0]['plugin_settings']['showAppleEvents'] = value
    path = configure(monkeypatch, tmp_path, original)
    before = path.read_bytes()
    Config()
    assert path.read_bytes() == before


@pytest.mark.parametrize('field,value', [
    ('name', 'OtherCalendar'), ('plugin_id', 'clock'), ('instance_uuid', '0c8ef20d-9d91-4e55-b766-21a405e6be74'),
    ('structural_generation', 2), ('settings_revision', 3), ('refresh', {'interval': 1800}),
])
def test_nonmatching_identity_is_untouched(monkeypatch, tmp_path, field, value):
    original = payload()
    original['playlist_config']['playlists'][0]['plugins'][0][field] = value
    path = configure(monkeypatch, tmp_path, original)
    before = path.read_bytes()
    Config()
    assert path.read_bytes() == before


def test_other_installation_and_completed_marker_are_untouched(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path, payload(), bind=False)
    before = path.read_bytes()
    Config()
    assert path.read_bytes() == before
    original = payload()
    original['runtime_migrations'][migration.MIGRATION_ID] = True
    path = configure(monkeypatch, tmp_path, original)
    before = path.read_bytes()
    Config()
    assert path.read_bytes() == before


def test_concurrent_write_is_preserved_and_migration_not_marked(monkeypatch, tmp_path):
    path = configure(monkeypatch, tmp_path, payload(), bind=False)
    config = Config()
    monkeypatch.setattr(migration, 'TARGET_UUID_HASH', hashlib.sha256(UUID.encode()).hexdigest()[:16])
    capture = config.capture_detached_playlist_transaction

    def race():
        transaction = capture()
        config.update_config({'concurrent_value': 'preserve me'})
        return transaction

    monkeypatch.setattr(config, 'capture_detached_playlist_transaction', race)
    with pytest.raises(ConfigConflictError):
        migration.apply_saved_apple_calendar_migration(config)
    saved = json.loads(path.read_text())
    assert saved['concurrent_value'] == 'preserve me'
    assert migration.MIGRATION_ID not in saved['runtime_migrations']
    assert 'showAppleEvents' not in saved['playlist_config']['playlists'][0]['plugins'][0]['plugin_settings']
