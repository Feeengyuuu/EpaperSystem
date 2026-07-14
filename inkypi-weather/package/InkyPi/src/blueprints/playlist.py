import logging
from datetime import datetime, timedelta, timezone

from flask import Blueprint, current_app, jsonify, render_template, request

from blueprints.plugin import (
    _cancel_instance_work,
    _cleanup_plugin_instance_snapshot,
    _discard_instance_retry,
)
from security.request_limits import UploadError
from utils.app_utils import (
    RequestFileReferenceError,
    parse_form,
    prepare_request_files,
    validate_request_file_references,
)
from utils.refresh_validation import (
    RefreshValidationError,
    parse_refresh_config,
    validation_error_payload,
)


logger = logging.getLogger(__name__)
playlist_bp = Blueprint("playlist", __name__)


def _signal_config_change():
    refresh_task = current_app.config.get("REFRESH_TASK")
    if refresh_task and hasattr(refresh_task, "signal_config_change"):
        refresh_task.signal_config_change()


@playlist_bp.route('/add_plugin', methods=['POST'])
def add_plugin():
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    prepared_files = None
    try:
        plugin_settings = parse_form(request.form)
        parsed_refresh = parse_refresh_config(
            plugin_settings.pop("refresh_settings", None)
        )
        plugin_id = plugin_settings.pop("plugin_id", None)
        if not plugin_id:
            return jsonify({"error": "Plugin id is required"}), 400

        playlist = parsed_refresh.request.get('playlist')
        instance_name = parsed_refresh.request.get('instance_name')
        if not playlist:
            return jsonify({"error": "Playlist name is required"}), 400
        if not instance_name or not instance_name.strip():
            return jsonify({"error": "Instance name is required"}), 400
        if not all(char.isalpha() or char.isspace() or char.isnumeric() for char in instance_name):
            return jsonify({"error": "Instance name can only contain alphanumeric characters and spaces"}), 400

        prepared_files = prepare_request_files(request.files)
        plugin_settings.update(prepared_files.locations)
        plugin_dict = {
            "plugin_id": plugin_id,
            "refresh": dict(parsed_refresh.refresh),
            "plugin_settings": plugin_settings,
            "name": instance_name
        }
        with playlist_manager.instance_lifecycle_guard():
            prepared_files.promote()
            validate_request_file_references(plugin_settings)
            result = playlist_manager.add_plugin_to_playlist_snapshot(
                playlist,
                plugin_dict,
            )
            if not result:
                prepared_files.rollback()
                return jsonify({"error": "Failed to add to playlist"}), 400

            # The live model owns these files as soon as its mutation commits.
            # A later persistence failure must not leave dangling live settings.
            prepared_files.accept()
            device_config.write_config()
        _signal_config_change()
    except RefreshValidationError as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return jsonify(validation_error_payload(error)), 400
    except RequestFileReferenceError as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return jsonify({"error": str(error)}), 400
    except UploadError:
        if prepared_files is not None:
            prepared_files.rollback()
        raise
    except Exception as error:
        if prepared_files is not None:
            prepared_files.rollback()
        logger.exception("Add plugin failed: %s", error)
        return jsonify({"error": f"An error occurred: {error}"}), 500
    return jsonify({"success": True, "message": "Scheduled refresh configured."})

def _timestamp_sort_key(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _latest_success_timestamp(runtime_instance, fallback):
    """Newest content-render success across runtime lanes and legacy config.

    The theme lane is excluded: a theme-only redraw changes presentation, not
    content, so it must not advance the page's "Refreshed" badge.
    """
    candidates = []
    if runtime_instance is not None:
        lanes = (
            runtime_instance.data,
            runtime_instance.live,
            runtime_instance.presentation,
        )
        candidates.extend(
            lane.last_success_at for lane in lanes if lane.last_success_at
        )
        if runtime_instance.legacy_cache_success_at:
            candidates.append(runtime_instance.legacy_cache_success_at)
    if fallback:
        candidates.append(fallback)
    if not candidates:
        return None
    return max(candidates, key=_timestamp_sort_key)


def _overlay_runtime_refresh_times(playlist_config, refresh_task):
    """Merge runtime success timestamps into the serialized playlist payload.

    Successful refreshes are persisted in the runtime state store, not the
    playlist config, so the page must read both and show the newest.
    """
    try:
        runtime_instances = refresh_task.runtime_state.snapshot().instances
    except Exception:
        return playlist_config
    for playlist in playlist_config.get("playlists", []):
        for plugin in playlist.get("plugins", []):
            merged = _latest_success_timestamp(
                runtime_instances.get(plugin.get("instance_uuid")),
                plugin.get("latest_refresh_time"),
            )
            if merged:
                plugin["latest_refresh_time"] = merged
    return playlist_config


@playlist_bp.route('/playlist')
def playlists():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()
    refresh_info = device_config.get_refresh_info()
    plugins_list = device_config.get_plugins()

    return render_template(
        'playlist.html',
        playlist_config=_overlay_runtime_refresh_times(
            playlist_manager.to_dict(),
            refresh_task,
        ),
        refresh_info=refresh_info.to_dict(),
        plugins={p["id"]: p for p in plugins_list}
    )

@playlist_bp.route('/create_playlist', methods=['POST'])
def create_playlist():
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json(silent=True) or {}
    playlist_name = data.get("playlist_name")
    start_time = data.get("start_time")
    end_time = data.get("end_time")

    if not playlist_name or not playlist_name.strip():
        return jsonify({"error": "Playlist name is required"}), 400
    if not start_time or not end_time:
        return jsonify({"error": "Start time and End time are required"}), 400

    try:
        result = playlist_manager.add_playlist(playlist_name, start_time, end_time)
        if not result:
            # add_playlist is atomic; a False here means another request created it first
            return jsonify({"error": f"Playlist with name '{playlist_name}' already exists"}), 400

        # save changes to device config file
        device_config.write_config()
        _signal_config_change()

    except Exception as e:
        logger.exception("EXCEPTION CAUGHT: " + str(e))
        return jsonify({"error": f"An error occurred: {str(e)}"}), 500

    return jsonify({"success": True, "message": "Created new Playlist!"})


@playlist_bp.route('/update_playlist/<string:playlist_name>', methods=['PUT'])
def update_playlist(playlist_name):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json(silent=True) or {}

    new_name = data.get("new_name")
    start_time = data.get("start_time")
    end_time = data.get("end_time")
    if not new_name or not start_time or not end_time:
        return jsonify({"success": False, "error": "Missing required fields"}), 400

    result = playlist_manager.update_playlist(playlist_name, new_name, start_time, end_time)
    if not result:
        return jsonify({"error": f"Could not update playlist '{playlist_name}'; the target name may already exist"}), 400
    device_config.write_config()
    _signal_config_change()

    return jsonify({"success": True, "message": f"Updated playlist '{playlist_name}'!"})

@playlist_bp.route('/delete_playlist/<string:playlist_name>', methods=['DELETE'])
def delete_playlist(playlist_name):
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    if not playlist_name:
        return jsonify({"error": f"Playlist name is required"}), 400

    try:
        with playlist_manager.instance_lifecycle_guard():
            deleted = playlist_manager.delete_playlist_atomic(playlist_name)
            if deleted is None:
                return jsonify({
                    "error": f"Playlist '{playlist_name}' does not exist"
                }), 400

            for snapshot in deleted.removed_instances:
                _cancel_instance_work(refresh_task, snapshot.instance_uuid)
            for snapshot in deleted.removed_instances:
                _discard_instance_retry(refresh_task, snapshot.instance_uuid)
            device_config.write_config()
            for snapshot in deleted.removed_instances:
                _cleanup_plugin_instance_snapshot(
                    device_config,
                    refresh_task,
                    snapshot,
                )
        _signal_config_change()
    except Exception as error:
        logger.exception("Playlist deletion failed: %s", error)
        return jsonify({"error": f"An error occurred: {error}"}), 500

    return jsonify({"success": True, "message": f"Deleted playlist '{playlist_name}'!"})

@playlist_bp.app_template_filter('format_relative_time')
def format_relative_time(iso_date_string):
    # Parse the input ISO date string
    dt = datetime.fromisoformat(iso_date_string)

    # Get the timezone from the parsed datetime
    if dt.tzinfo is None:
        raise ValueError("Input datetime doesn't have a timezone.")

    # Get the current time in the same timezone as the input datetime
    now = datetime.now(dt.tzinfo)
    delta = now - dt

    # Compute time difference
    diff_seconds = delta.total_seconds()
    diff_minutes = diff_seconds / 60

    # Define formatting
    time_format = "%I:%M %p"  # Example: 04:30 PM
    month_day_format = "%b %d at " + time_format  # Example: Feb 12 at 04:30 PM

    # Determine relative time string
    if diff_seconds < 120:
        return "just now"
    elif diff_minutes < 60:
        return f"{int(diff_minutes)} minutes ago"
    elif dt.date() == now.date():
        return "today at " + dt.strftime(time_format).lstrip("0")
    elif dt.date() == (now.date() - timedelta(days=1)):
        return "yesterday at " + dt.strftime(time_format).lstrip("0")
    else:
        return dt.strftime(month_day_format).replace(" 0", " ")  # Removes leading zero in day
