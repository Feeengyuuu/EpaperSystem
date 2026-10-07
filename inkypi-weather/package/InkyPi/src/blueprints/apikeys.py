from flask import Blueprint, request, jsonify, current_app, has_app_context, render_template
from dotenv import dotenv_values
import os
import re
import tempfile
import logging
from secret_schema import SecretSchema

logger = logging.getLogger(__name__)
apikeys_bp = Blueprint("apikeys", __name__)
_SECRET_SCHEMA = SecretSchema.load()

# Path to .env file
def get_env_path():
    """Get the canonical runtime env path, with a legacy standalone fallback."""
    if has_app_context():
        runtime_paths = current_app.config.get("RUNTIME_PATHS")
        if runtime_paths is not None:
            return runtime_paths.env_file
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
    return os.path.join(base_dir, '.env')


def get_api_key_registry():
    """Return validated API key metadata from the canonical SecretSchema."""
    return _SECRET_SCHEMA.public_registry()


def parse_env_file(filepath):
    """Parse .env file and return list of (key, value) tuples."""
    if not os.path.exists(filepath):
        return []
    
    try:
        env_dict = dotenv_values(filepath)
        return list(env_dict.items())
    except Exception as e:
        logger.error(f"Error parsing .env file: {e}")
        return []


def _format_env_value(value):
    value = "" if value is None else str(value)
    needs_quotes = (
        value == ""
        or value != value.strip()
        or any(char.isspace() for char in value)
        or any(char in value for char in ['"', "'", "\\", "#"])
    )
    if not needs_quotes:
        return value

    escaped = (
        value
        .replace("\\", "\\\\")
        .replace("\r", "\\r")
        .replace("\n", "\\n")
        .replace('"', '\\"')
    )
    return f'"{escaped}"'


def write_env_file(filepath, entries):
    """Write entries to .env file using an atomic same-directory replace."""
    tmp_path = None

    def write_entries(target_path):
        with open(target_path, 'w', encoding="utf-8", newline="\n") as f:
            f.write("# InkyPi API Keys and Secrets\n")
            f.write("# Managed via web interface\n\n")
            for key, value in entries:
                f.write(f"{key}={_format_env_value(value)}\n")

    try:
        env_dir = os.path.dirname(filepath) or "."
        os.makedirs(env_dir, exist_ok=True)
        if os.name == "nt":
            write_entries(filepath)
            return True
        fd, tmp_path = tempfile.mkstemp(prefix=".env.", suffix=".tmp", dir=env_dir)
        os.close(fd)
        write_entries(tmp_path)
        try:
            os.replace(tmp_path, filepath)
            tmp_path = None
        except OSError:
            logger.exception("Atomic .env replace failed; falling back to direct write: %s", filepath)
            write_entries(filepath)
        return True
    except Exception as e:
        logger.error(f"Error writing .env file: {e}")
        return False
    finally:
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                logger.warning("Could not remove temporary .env file: %s", tmp_path)


def _has_value(value):
    return value is not None and str(value) != ""


def build_key_services(entries, registry, used_features=()):
    """Group .env entries by the service that consumes them.

    Returns names and states only; values never leave this function.
    ``env_key`` is the variable actually in use (canonical name or alias),
    so replacing a key keeps writing the name the plugins already read.
    """
    values = {}
    order = []
    for key, value in entries:
        if key not in values:
            order.append(key)
        values[key] = value
    used = {str(feature).casefold() for feature in used_features}

    known = set()
    groups = {"needed": [], "configured": [], "available": []}
    for item in registry:
        aliases = list(item.get("aliases") or [])
        names = [item["key"], *aliases]
        known.update(names)
        present = [name for name in names if _has_value(values.get(name))]
        listed = [name for name in names if name in values]
        features = list(item.get("features") or [])
        in_use = any(feature.casefold() in used for feature in features)
        service = {
            "key": item["key"],
            "service": item.get("service") or item["key"],
            "features": features,
            "aliases": aliases,
            "signup_url": item.get("signup_url") or "",
            "notes": item.get("notes") or "",
            "value_type": item.get("value_type") or "secret",
            "configured": bool(present),
            "in_use": in_use,
            "env_key": (present or listed or [item["key"]])[0],
        }
        if present:
            groups["configured"].append(service)
        elif in_use:
            groups["needed"].append(service)
        else:
            groups["available"].append(service)

    groups["other"] = [
        {"key": key, "configured": _has_value(values[key])}
        for key in order
        if key not in known
    ]
    groups["env_keys"] = order
    return groups


def key_service_status(expected_key, entries, registry):
    """Configured state of the service that owns ``expected_key``."""
    services = build_key_services(entries, registry)
    for service in services["configured"] + services["needed"] + services["available"]:
        if expected_key == service["key"] or expected_key in service["aliases"]:
            return service
    value = dict(entries).get(expected_key)
    return {
        "key": expected_key,
        "service": expected_key,
        "aliases": [],
        "configured": _has_value(value),
        "env_key": expected_key,
    }


def env_entries():
    """Current (key, value) pairs of the runtime env file."""
    return parse_env_file(get_env_path())


def _used_plugin_features():
    """Display names of plugins that sit in any playlist."""
    try:
        device_config = current_app.config["DEVICE_CONFIG"]
        names = {plugin["id"]: plugin.get("display_name") for plugin in device_config.get_plugins()}
        playlists = device_config.get_playlist_manager().to_dict().get("playlists", [])
    except Exception:
        return set()
    return {
        names.get(instance.get("plugin_id")) or instance.get("plugin_id")
        for playlist in playlists
        for instance in playlist.get("plugins", [])
    }


@apikeys_bp.route('/api-keys')
def apikeys_page():
    """Render API keys grouped by service. Values are never sent to the page."""
    env_path = get_env_path()
    services = build_key_services(
        parse_env_file(env_path),
        get_api_key_registry(),
        _used_plugin_features(),
    )
    return render_template(
        'apikeys.html',
        services=services,
        env_exists=os.path.exists(env_path),
    )


@apikeys_bp.route('/api-keys/save', methods=['POST'])
def save_apikeys():
    """Save API keys to .env file."""
    try:
        data = request.get_json()
        entries = data.get('entries', [])
        
        # Load existing values for keys marked as keepExisting
        env_path = get_env_path()
        existing_values = dict(parse_env_file(env_path))
        
        # Validate and process entries
        valid_entries = []
        for entry in entries:
            key = entry.get('key', '').strip()
            keep_existing = entry.get('keepExisting', False)
            
            if not key:
                continue
            
            # Validate key format
            if not re.match(r'^[A-Za-z_][A-Za-z0-9_]*$', key):
                return jsonify({"error": f"Invalid key format: {key}"}), 400
            
            if keep_existing:
                # Use existing value from .env file
                value = existing_values.get(key, '')
            else:
                # Use provided value
                value = entry.get('value', '').strip()
            
            valid_entries.append((key, value))
        
        if write_env_file(env_path, valid_entries):
            # Reload environment variables
            for key, value in valid_entries:
                os.environ[key] = value
            
            return jsonify({
                "success": True,
                "message": f"Saved {len(valid_entries)} API key(s). Some plugins may require restart to pick up changes."
            })
        else:
            return jsonify({"error": "Failed to write .env file"}), 500
            
    except Exception as e:
        logger.error(f"Error saving API keys: {e}")
        return jsonify({"error": str(e)}), 500
