from datetime import datetime, timezone
from pathlib import Path

from flask import Blueprint, current_app, jsonify, render_template, request, send_file

from blueprints.playlist import now_playing_payload, render_now_playing

main_bp = Blueprint("main", __name__)


@main_bp.app_context_processor
def inkypi_shell_context():
    """Device name for the navigation shell on every page."""
    device_config = current_app.config.get("DEVICE_CONFIG")
    name = None
    try:
        name = device_config.get_config("name")
    except TypeError:
        name = (device_config.get_config() or {}).get("name")
    except Exception:
        name = None
    return {"inkypi_device_name": name or "InkyPi"}


@main_bp.route('/')
def main_page():
    return render_now_playing()


@main_bp.route('/plugins')
def plugins_page():
    device_config = current_app.config['DEVICE_CONFIG']
    return render_template('plugins.html', plugins=device_config.get_plugins())


@main_bp.route('/api/now-playing')
def now_playing():
    """Read-only summary of what the display shows now."""
    device_config = current_app.config['DEVICE_CONFIG']
    plugins = {p["id"]: p for p in device_config.get_plugins()}
    payload = now_playing_payload(device_config.get_refresh_info().to_dict(), plugins)
    response = jsonify(payload)
    response.headers["Cache-Control"] = "no-store"
    return response


@main_bp.route('/api/current_image')
def get_current_image():
    """Serve only the image named by the authoritative display manifest."""

    display_manager = current_app.config.get("DISPLAY_MANAGER")
    transaction = getattr(display_manager, "transaction", None)
    commit = transaction.current() if transaction is not None else None
    if commit is None or not Path(commit.image_path).is_file():
        return jsonify({"error": "Image not found"}), 404

    try:
        last_modified = datetime.fromisoformat(commit.committed_at)
    except (TypeError, ValueError):
        return jsonify({"error": "Image not found"}), 404
    if last_modified.tzinfo is None:
        last_modified = last_modified.replace(tzinfo=timezone.utc)

    response = send_file(
        commit.image_path,
        mimetype="image/png",
        conditional=True,
        etag=commit.commit_id,
        last_modified=last_modified,
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


@main_bp.route('/api/plugin_order', methods=['POST'])
def save_plugin_order():
    """Save the custom plugin order."""
    device_config = current_app.config['DEVICE_CONFIG']

    data = request.get_json() or {}
    order = data.get('order', [])

    if not isinstance(order, list):
        return jsonify({"error": "Order must be a list"}), 400

    device_config.set_plugin_order(order)

    return jsonify({"success": True})
