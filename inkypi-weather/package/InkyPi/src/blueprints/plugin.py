import logging
import os
from collections.abc import Mapping
from enum import Enum

from flask import Blueprint, current_app, jsonify, render_template, request, send_from_directory

from plugins.plugin_registry import (
    get_plugin_instance,
    plugin_supports_day_night_theme,
)
from refresh_task import ManualRefresh
from runtime.refresh_contracts import JobRecord, thaw_payload
from runtime.refresh_queue import QueueFullError, QueueStoppingError
from security.request_limits import UploadError
from utils.app_utils import (
    RequestFileReferenceError,
    parse_form,
    prepare_request_files,
    resolve_path,
    validate_request_file_references,
)
from utils.refresh_validation import (
    RefreshValidationError,
    parse_refresh_config,
    validation_error_payload,
)
from utils.theme_utils import normalize_theme_mode

logger = logging.getLogger(__name__)
plugin_bp = Blueprint("plugin", __name__)
_PLUGIN_THEME_SETTING_KEYS = (
    "themeMode",
    "theme_mode",
    "theme",
    "sportsDashboardTheme",
)


def _plugin_theme_mode(plugin_settings):
    if not isinstance(plugin_settings, Mapping):
        return "auto"
    for key in _PLUGIN_THEME_SETTING_KEYS:
        if key in plugin_settings:
            return normalize_theme_mode(plugin_settings[key], "auto") or "auto"
    return "auto"


def _signal_config_change():
    refresh_task = current_app.config.get("REFRESH_TASK")
    if refresh_task and hasattr(refresh_task, "signal_config_change"):
        refresh_task.signal_config_change()


def _serialize_job(job):
    if job is None:
        return None
    if isinstance(job, Mapping):
        payload = dict(job)
    elif isinstance(job, JobRecord):
        payload = {
            "id": job.id,
            "command_id": job.command_id,
            "status": job.status,
            "submitted_at": job.submitted_at,
        }
        for key in (
            "started_at",
            "completed_at",
            "cancel_requested_at",
            "superseded_by",
            "error_code",
            "error",
        ):
            value = getattr(job, key)
            if value is not None:
                payload[key] = value
    else:
        raise TypeError(f"Unsupported refresh job payload: {type(job).__name__}")
    return {
        key: value.value if isinstance(value, Enum) else value
        for key, value in payload.items()
    }


def _error_response(message, error_code, status, *, job=None, retry_after=None):
    serialized_job = _serialize_job(job)
    response = jsonify({
        "success": False,
        "error_code": error_code,
        "error": message,
        "message": message,
        "job": serialized_job,
        "job_id": serialized_job.get("id") if serialized_job else None,
    })
    response.status_code = status
    if retry_after is not None:
        response.headers["Retry-After"] = str(retry_after)
    return response


def _queue_error_response(error):
    if isinstance(error, QueueFullError):
        status = 429
    else:
        status = 503
    return _error_response(
        str(error),
        error.error_code,
        status,
        job=getattr(error, "job", None),
        retry_after=5,
    )


def _queued_refresh_response(job):
    serialized_job = _serialize_job(job)
    if serialized_job.get("status") == "rejected":
        error_code = serialized_job.get("error_code") or "refresh_rejected"
        message = serialized_job.get("error") or "Refresh request was rejected"
        status_by_error_code = {
            QueueFullError.error_code: 429,
            QueueStoppingError.error_code: 503,
        }
        status = status_by_error_code.get(error_code, 400)
        retry_after = 5 if error_code in status_by_error_code else None
        return _error_response(
            message,
            error_code,
            status,
            job=serialized_job,
            retry_after=retry_after,
        )
    return jsonify({
        "success": True,
        "message": "Display update queued",
        "job": serialized_job,
        "job_id": serialized_job.get("id"),
        "status_url": f"/refresh_job/{serialized_job.get('id')}",
    }), 202


def _validation_error_response(error):
    return jsonify(validation_error_payload(error)), 400


def _instance_image_filename(snapshot):
    return f"{snapshot.plugin_id}_{snapshot.name.replace(' ', '_')}.png"


def _cleanup_plugin_instance_snapshot(device_config, refresh_task, snapshot):
    """Best-effort cleanup of one durably deleted immutable instance."""
    plugin_id = str(snapshot.plugin_id).strip()
    try:
        playlist_manager = device_config.get_playlist_manager()
        with playlist_manager.instance_lifecycle_guard():
            context = refresh_task.make_cleanup_context()
            with refresh_task.render_arbiter.lease(plugin_id, context):
                # Enumerate only after acquiring the same lease used by cache
                # promotion. Otherwise a stale renderer can publish after the
                # cleanup snapshot and leave an orphaned authoritative cache.
                managed_paths = set(refresh_task.managed_cache_paths(
                    snapshot.instance_uuid,
                    plugin_id=plugin_id,
                    instance_name=snapshot.name,
                ))
                replacement = playlist_manager.resolve_plugin_instance_snapshot(
                    None,
                    plugin_id,
                    snapshot.name,
                )
                if replacement is None:
                    try:
                        plugin_config = device_config.get_plugin(plugin_id)
                        if plugin_config:
                            plugin = get_plugin_instance(plugin_config)
                            plugin.cleanup(thaw_payload(snapshot.settings))
                    except Exception as error:
                        logger.warning(
                            "Plugin cleanup failed for '%s' instance %s: %s",
                            plugin_id,
                            snapshot.instance_uuid,
                            error,
                        )
                else:
                    logger.info(
                        "Skipping opaque plugin cleanup because replacement owns "
                        "legacy identity. | plugin_id: %s | old_uuid: %s | new_uuid: %s",
                        plugin_id,
                        snapshot.instance_uuid,
                        replacement.instance.instance_uuid,
                    )

                # The old name-based PNG is compatibility-only. Delete it only
                # while no replacement owns the same legacy identity. A replacement
                # render uses the same arbiter, so it cannot publish the alias
                # between this check and deletion.
                if replacement is None:
                    managed_paths.add(os.path.join(
                        device_config.plugin_image_dir,
                        _instance_image_filename(snapshot),
                    ))

                for path in sorted(managed_paths):
                    try:
                        os.remove(path)
                        logger.info("Deleted plugin instance cache: %s", path)
                    except FileNotFoundError:
                        continue
                    except OSError as error:
                        logger.warning(
                            "Failed to delete plugin instance cache %s: %s",
                            path,
                            error,
                        )
    except Exception as error:
        logger.warning(
            "Cleanup lease failed for '%s' instance %s: %s",
            plugin_id,
            snapshot.instance_uuid,
            error,
        )


def _cancel_instance_work(refresh_task, instance_uuid):
    refresh_task.refresh_queue.cancel_instance(instance_uuid)


def _discard_instance_retry(refresh_task, instance_uuid):
    refresh_task.retry_registry.discard(instance_uuid)


def _legacy_lookup_error(message):
    return jsonify({
        "success": False,
        "error": message,
        "message": message,
    }), 400


def _server_error(error):
    message = f"An error occurred: {error}"
    return jsonify({"error": message, "message": message}), 500

# Removed module-level PLUGINS_DIR - will resolve dynamically in route handlers

@plugin_bp.route('/plugin/<plugin_id>')
def plugin_page(plugin_id):
    device_config = current_app.config['DEVICE_CONFIG']
    playlist_manager = device_config.get_playlist_manager()

    # Find the plugin by id
    plugin_config = device_config.get_plugin(plugin_id)
    if plugin_config:
        try:
            plugin = get_plugin_instance(plugin_config)
            template_params = plugin.generate_settings_template()
            template_params["supports_day_night_theme"] = (
                plugin_supports_day_night_theme(plugin_config)
            )

            # retrieve plugin instance from the query parameters if updating existing plugin instance
            plugin_instance_name = request.args.get('instance')
            if plugin_instance_name:
                selection = playlist_manager.resolve_plugin_instance_snapshot(
                    None,
                    plugin_id,
                    plugin_instance_name,
                )
                if not selection:
                    return jsonify({"error": f"Plugin instance: {plugin_instance_name} does not exist"}), 500

                # add plugin instance settings to the template to prepopulate
                template_params["plugin_settings"] = thaw_payload(
                    selection.instance.settings
                )
                template_params["plugin_instance"] = plugin_instance_name
                template_params["refresh_settings"] = thaw_payload(
                    selection.instance.refresh
                )

            template_params["plugin_theme_mode"] = _plugin_theme_mode(
                template_params.get("plugin_settings")
            )
            template_params["playlists"] = playlist_manager.get_playlist_names()
        except Exception as e:
            logger.exception("EXCEPTION CAUGHT: " + str(e))
            return jsonify({"error": f"An error occurred: {str(e)}"}), 500
        return render_template('plugin.html', plugin=plugin_config, **template_params)
    else:
        return "Plugin not found", 404

@plugin_bp.route('/images/<plugin_id>/<path:filename>')
def image(plugin_id, filename):
    # Resolve plugins directory dynamically
    plugins_dir = resolve_path("plugins")

    # Construct the full path to the plugin's file
    plugin_dir = os.path.join(plugins_dir, plugin_id)

    # Security check to prevent directory traversal
    safe_path = os.path.abspath(os.path.join(plugin_dir, filename))
    if not safe_path.startswith(os.path.abspath(plugins_dir)):
        return "Invalid path", 403

    # Convert to absolute path for send_from_directory
    abs_plugin_dir = os.path.abspath(plugin_dir)

    # Check if the directory and file exist
    if not os.path.isdir(abs_plugin_dir):
        logger.error(f"Plugin directory not found: {abs_plugin_dir}")
        return "Plugin directory not found", 404

    if not os.path.isfile(safe_path):
        logger.error(f"File not found: {safe_path}")
        return "File not found", 404

    # Serve the file from the plugin directory
    return send_from_directory(abs_plugin_dir, filename)

@plugin_bp.route('/plugin_instance_image/<path:playlist_name>/<path:plugin_id>/<path:instance_name>')
def plugin_instance_image(playlist_name, plugin_id, instance_name):
    """Serve the generated image for a plugin instance."""
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    selection = playlist_manager.resolve_plugin_instance_snapshot(
        playlist_name,
        plugin_id,
        instance_name,
    )
    if not selection:
        return "Plugin instance not found", 404

    # Get the image path
    image_path = refresh_task.cache_path_for_snapshot(selection.instance)
    image_directory = os.path.dirname(image_path)
    image_filename = os.path.basename(image_path)

    # Check if the image exists
    if not os.path.exists(image_path):
        # Return a placeholder or 404
        return "Image not yet generated", 404

    # Serve the image
    return send_from_directory(image_directory, image_filename)

@plugin_bp.route('/delete_plugin_instance', methods=['POST'])
def delete_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json(silent=True) or {}
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    instance_name = data.get("plugin_instance")

    try:
        with playlist_manager.instance_lifecycle_guard():
            selection = playlist_manager.resolve_plugin_instance_snapshot(
                playlist_name,
                plugin_id,
                instance_name,
            )
            if selection is None:
                return _legacy_lookup_error("Plugin instance not found")

            snapshot = selection.instance
            mutation = playlist_manager.delete_plugin_instance_atomic(
                snapshot.instance_uuid,
                expected_generation=snapshot.structural_generation,
                expected_settings_revision=snapshot.settings_revision,
            )
            if mutation is None:
                return _legacy_lookup_error("Plugin instance changed; reload and retry")

            removed = mutation.old_snapshot
            _cancel_instance_work(refresh_task, removed.instance_uuid)
            _discard_instance_retry(refresh_task, removed.instance_uuid)
            device_config.write_config()
            _cleanup_plugin_instance_snapshot(device_config, refresh_task, removed)
        _signal_config_change()

    except Exception as error:
        logger.exception("Plugin instance deletion failed: %s", error)
        return _server_error(error)

    return jsonify({"success": True, "message": "Deleted plugin instance."})

@plugin_bp.route('/update_plugin_instance/<string:instance_name>', methods=['PUT'])
def update_plugin_instance(instance_name):
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    prepared_files = None
    try:
        form_data = parse_form(request.form)

        if not instance_name:
            raise RuntimeError("Instance name is required")

        plugin_id = form_data.pop("plugin_id", None)
        if not plugin_id:
            return _legacy_lookup_error("Plugin id is required")

        refresh_settings_json = form_data.pop("refresh_settings", None)
        parsed_refresh = None
        if refresh_settings_json is not None:
            parsed_refresh = parse_refresh_config(refresh_settings_json)

        selection = playlist_manager.resolve_plugin_instance_snapshot(
            None,
            plugin_id,
            instance_name,
        )
        if selection is None:
            return _legacy_lookup_error(
                f"Plugin instance: {instance_name} does not exist"
            )

        prepared_files = prepare_request_files(request.files, request.form)
        plugin_settings = form_data
        plugin_settings.update(prepared_files.locations)
        snapshot = selection.instance
        with playlist_manager.instance_lifecycle_guard():
            prepared_files.promote()
            validate_request_file_references(plugin_settings)
            mutation = playlist_manager.update_plugin_instance_atomic(
                snapshot.instance_uuid,
                settings=plugin_settings or None,
                refresh=dict(parsed_refresh.refresh) if parsed_refresh else None,
                expected_generation=snapshot.structural_generation,
                expected_settings_revision=snapshot.settings_revision,
            )
            if mutation is None:
                prepared_files.rollback()
                return _legacy_lookup_error("Plugin instance changed; reload and retry")

            # Mutation is the ownership transfer point for uploaded resources.
            prepared_files.accept()
            _cancel_instance_work(refresh_task, mutation.old_snapshot.instance_uuid)
            device_config.write_config()
        _signal_config_change()
    except RefreshValidationError as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return _validation_error_response(error)
    except RequestFileReferenceError as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return _legacy_lookup_error(str(error))
    except UploadError:
        if prepared_files is not None:
            prepared_files.rollback()
        raise
    except Exception as error:
        if prepared_files is not None:
            prepared_files.rollback()
        logger.exception("Plugin instance update failed: %s", error)
        return _server_error(error)
    return jsonify({"success": True, "message": f"Updated plugin instance {instance_name}."})

@plugin_bp.route('/display_plugin_instance', methods=['POST'])
def display_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json(silent=True) or {}
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance_name = data.get("plugin_instance")

    try:
        selection = playlist_manager.resolve_plugin_instance_snapshot(
            playlist_name,
            plugin_id,
            plugin_instance_name,
        )
        if selection is None:
            return _legacy_lookup_error(
                f"Plugin instance '{plugin_instance_name}' not found"
            )
        snapshot = selection.instance

        job = refresh_task.submit_playlist_display(
            snapshot.instance_uuid,
            force=False,
            display_cached_only=True,
            force_hardware_write=True,
            expected_playlist_name=selection.playlist_name,
            expected_generation=snapshot.structural_generation,
            expected_settings_revision=snapshot.settings_revision,
            require_active=False,
        )
        return _queued_refresh_response(job)
    except (QueueFullError, QueueStoppingError) as error:
        return _queue_error_response(error)
    except (RuntimeError, ValueError) as error:
        return _error_response(str(error), "refresh_rejected", 400)
    except Exception as error:
        logger.exception("Display instance submission failed: %s", error)
        return _server_error(error)


@plugin_bp.route('/refresh_plugin_instance', methods=['POST'])
def refresh_plugin_instance():
    device_config = current_app.config['DEVICE_CONFIG']
    refresh_task = current_app.config['REFRESH_TASK']
    playlist_manager = device_config.get_playlist_manager()

    data = request.get_json(silent=True) or {}
    playlist_name = data.get("playlist_name")
    plugin_id = data.get("plugin_id")
    plugin_instance_name = data.get("plugin_instance")

    try:
        selection = playlist_manager.resolve_plugin_instance_snapshot(
            playlist_name,
            plugin_id,
            plugin_instance_name,
        )
        if selection is None:
            return _legacy_lookup_error(
                f"Plugin instance '{plugin_instance_name}' not found"
            )
        snapshot = selection.instance

        job = refresh_task.submit_playlist_data_refresh(
            snapshot.instance_uuid,
            expected_playlist_name=selection.playlist_name,
            expected_generation=snapshot.structural_generation,
            expected_settings_revision=snapshot.settings_revision,
            require_active=False,
        )
        return _queued_refresh_response(job)
    except (QueueFullError, QueueStoppingError) as error:
        return _queue_error_response(error)
    except (RuntimeError, ValueError) as error:
        return _error_response(str(error), "refresh_rejected", 400)
    except Exception as error:
        logger.exception("Data refresh instance submission failed: %s", error)
        return _server_error(error)


@plugin_bp.route('/update_now', methods=['POST'])
def update_now():
    refresh_task = current_app.config['REFRESH_TASK']

    prepared_files = None
    try:
        plugin_settings = parse_form(request.form)
        plugin_id = plugin_settings.pop("plugin_id", None)
        if not plugin_id:
            return _legacy_lookup_error("Plugin id is required")
        prepared_files = prepare_request_files(request.files)
        plugin_settings.update(prepared_files.locations)
        prepared_files.promote()
        validate_request_file_references(plugin_settings)
        job = refresh_task.submit_manual_update(
            ManualRefresh(plugin_id, plugin_settings),
            transient_paths=tuple(prepared_files.promoted),
        )
        serialized_job = _serialize_job(job)
        if serialized_job.get("status") == "rejected":
            prepared_files.rollback()
        else:
            prepared_files.accept()
        return _queued_refresh_response(job)
    except (QueueFullError, QueueStoppingError) as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return _queue_error_response(error)
    except RequestFileReferenceError as error:
        if prepared_files is not None:
            prepared_files.rollback()
        return _legacy_lookup_error(str(error))
    except UploadError:
        if prepared_files is not None:
            prepared_files.rollback()
        raise
    except Exception as error:
        if prepared_files is not None:
            prepared_files.rollback()
        logger.exception("Error in update_now: %s", error)
        return _server_error(error)


@plugin_bp.route('/refresh_job/<job_id>', methods=['GET'])
def refresh_job(job_id):
    refresh_task = current_app.config['REFRESH_TASK']
    job = refresh_task.get_manual_update_job(job_id) if hasattr(refresh_task, "get_manual_update_job") else None
    if not job:
        return jsonify({"success": False, "message": "Refresh job not found"}), 404
    return jsonify({"success": True, "job": _serialize_job(job)}), 200
