import logging
import time
from pathlib import Path

from utils.http_client import get_http_client

logger = logging.getLogger(__name__)

DATASET_URL = "https://raw.githubusercontent.com/JohannesNE/literature-clock/master/litclock_annotated.csv"
MAX_AGE_SECONDS = 7 * 24 * 60 * 60  # 7 days


def ensure_dataset(csv_path: Path) -> None:
    """Ensure CSV exists and is fresh.

    - If file is fresh, do nothing.
    - If stale, attempt download; on failure keep the existing file.
    - If missing, attempt download; on failure raise FileNotFoundError.
    """
    csv_path = Path(csv_path)
    exists = csv_path.exists()
    fresh = exists and (time.time() - csv_path.stat().st_mtime) < MAX_AGE_SECONDS

    if fresh:
        return

    try:
        response = get_http_client().request_text(
            "GET",
            DATASET_URL,
            timeout=15,
            max_bytes=16 * 1024 * 1024,
        )
        csv_path.parent.mkdir(parents=True, exist_ok=True)
        csv_path.write_text(response.data, encoding="utf-8")
        return
    except Exception as exc:
        logger.warning("Literature clock dataset refresh failed: %s", exc)
        if not exists:
            raise FileNotFoundError(f"Literature clock dataset unavailable at {csv_path}") from exc
        # else: keep stale file
