"""Enable the requested Apple feed for the verified saved calendar once."""
from collections.abc import Mapping
import hashlib
import logging

from config_store import ConfigConflictError

logger = logging.getLogger(__name__)
MIGRATION_ID = 'saved_apple_calendar_follow_v1'
TARGET_UUID_HASH = '9b801b3a5c514dbf'


def _thaw(value):
    if isinstance(value, Mapping):
        return {key: _thaw(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_thaw(item) for item in value]
    return value


def apply_saved_apple_calendar_migration(config):
    markers = config.get_config('runtime_migrations', default={})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    version, authoritative, playlist, manager = config.capture_detached_playlist_transaction()
    markers = authoritative.get('runtime_migrations', {})
    if isinstance(markers, Mapping) and markers.get(MIGRATION_ID) is True:
        return
    selection = manager.resolve_plugin_instance_snapshot('DailyDoseOfDay', 'simple_calendar', 'Date')
    if selection is None:
        return
    before = selection.instance
    if (
        hashlib.sha256(before.instance_uuid.encode()).hexdigest()[:16] != TARGET_UUID_HASH
        or before.structural_generation != 1
        or before.settings_revision != 2
        or before.refresh.get('interval') != 3600
        or 'showAppleEvents' in before.settings
    ):
        return
    if not isinstance(markers, Mapping):
        raise ValueError('Apple calendar migration state is malformed')
    settings = {**_thaw(before.settings), 'showAppleEvents': 'true'}
    mutation = manager.update_plugin_instance_atomic(
        before.instance_uuid, settings=settings,
        expected_generation=before.structural_generation,
        expected_settings_revision=before.settings_revision,
    )
    if mutation is None or mutation.new_snapshot is None:
        raise ConfigConflictError(before.settings_revision, before.settings_revision)
    after = mutation.new_snapshot
    if after.structural_generation != before.structural_generation or after.settings_revision != before.settings_revision + 1:
        raise ValueError('Apple calendar migration changed instance identity')
    config.commit_detached_playlist_transaction(
        expected_config_version=version,
        expected_config_data=authoritative,
        expected_playlist_config=playlist,
        playlist_manager=manager,
        config_updates={'runtime_migrations': {**_thaw(markers), MIGRATION_ID: True}},
    )
    logger.info('Enabled Apple event synchronization for the verified saved Date calendar.')
