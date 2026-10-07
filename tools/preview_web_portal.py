"""Launch the loopback-only Model Y portal preview from the repository root."""

from __future__ import annotations

from pathlib import Path
import sys


SOURCE_ROOT = Path(__file__).resolve().parents[1] / "inkypi-weather" / "package" / "InkyPi" / "src"
sys.path.insert(0, str(SOURCE_ROOT))

try:
    from web_portal.preview import main  # noqa: E402
except ModuleNotFoundError as error:
    if error.name not in {"PIL", "flask", "werkzeug"}:
        raise
    raise SystemExit("缺少本地预览依赖。请先安装 tools/requirements-web-portal-preview.txt。") from None


if __name__ == "__main__":
    raise SystemExit(main())
