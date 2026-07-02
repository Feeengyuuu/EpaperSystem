import logging
import os
import secrets


logger = logging.getLogger(__name__)


def load_or_create_secret_key(path):
    """Load a persistent Flask secret key from path, creating it if needed.

    Returns the stripped file content when the file exists and is non-empty.
    Otherwise generates a strong token, persists it (best effort), and returns
    it. On any read/write error a fresh ephemeral token is returned so the app
    can still start with a strong key.
    """
    try:
        if os.path.isfile(path):
            with open(path, "r", encoding="utf-8") as handle:
                content = handle.read().strip()
            if content:
                return content

        key = secrets.token_hex(32)
        parent_dir = os.path.dirname(path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(key)
        try:
            os.chmod(path, 0o600)
        except OSError:
            # chmod is best effort; not meaningful on some platforms (e.g. Windows)
            pass
        return key
    except OSError:
        logger.warning("Could not read or persist secret key at %s; using an ephemeral key.", path)
        return secrets.token_hex(32)
