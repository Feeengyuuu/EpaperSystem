from pathlib import Path


def ensure_dataset(csv_path: Path) -> None:
    csv_path = Path(csv_path)
    if not csv_path.is_file():
        raise FileNotFoundError(f"Chinese literature clock dataset unavailable at {csv_path}")
