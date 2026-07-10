#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/.." && pwd -P)"
VENV_DIR="${VENV_DIR:-$PROJECT_ROOT/.venv}"
REQUIREMENTS_FILE="$PROJECT_ROOT/install/requirements-dev.txt"
SRC_DIR="$PROJECT_ROOT/src"
PYTHON_BIN="${PYTHON_BIN:-python3}"

python_minor_version() {
    "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

if ! command -v "$PYTHON_BIN" >/dev/null 2>&1; then
    echo "Python 3.11 is required, but '$PYTHON_BIN' was not found." >&2
    exit 1
fi

if [[ "$(python_minor_version "$PYTHON_BIN")" != "3.11" ]]; then
    echo "Python 3.11 is required. Set PYTHON_BIN to a Python 3.11 interpreter." >&2
    exit 1
fi

if [[ ! -d "$VENV_DIR" ]]; then
    echo "Creating Python 3.11 virtual environment in $VENV_DIR..."
    "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"

if [[ -z "${VIRTUAL_ENV:-}" || "$(python_minor_version python)" != "3.11" ]]; then
    echo "The existing virtual environment is not Python 3.11; recreate $VENV_DIR." >&2
    exit 1
fi

python -m pip install --no-cache-dir --require-hashes -r "$REQUIREMENTS_FILE"

export PYTHONDONTWRITEBYTECODE=1
export PYTHONPATH="$SRC_DIR${PYTHONPATH:+:$PYTHONPATH}"
export SRC_DIR

echo "Python 3.11 environment initialized; run 'deactivate' to exit."
