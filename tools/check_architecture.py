"""Enforce the extracted boundaries and ratchet the coordinator's size in CI."""

from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "inkypi-weather/package/InkyPi"
BOUNDARIES = {
    "runtime/refresh_planning.py": {
        "__future__", "dataclasses", "datetime", "typing", "model",
        "runtime.refresh_contracts", "runtime.refresh_policy", "runtime.runtime_state",
    },
    "runtime/plugin_execution.py": {
        "__future__", "contextlib", "dataclasses", "typing",
        "runtime.long_task_executor", "runtime.refresh_contracts",
    },
    "plugins/registry.py": {
        "__future__", "importlib", "logging", "pathlib", "threading", "typing",
    },
    "runtime/liveness_window.py": {
        "__future__", "dataclasses", "datetime", "enum", "hashlib", "logging", "typing",
    },
    "runtime/command_memory.py": {
        "__future__", "logging", "pathlib",
    },
    "runtime/overrun_recovery.py": {
        "__future__", "dataclasses", "datetime", "logging", "math", "time", "typing",
        "runtime.runtime_status",
    },
    "plugins/sports_dashboard/f1_domain.py": {
        "__future__", "collections.abc", "datetime", "typing",
    },
    "plugins/stocktracker/trend_chart.py": {
        "__future__", "collections.abc",
    },
}


def check_source(source: str, module: str) -> list[str]:
    try:
        tree = ast.parse(source)
    except SyntaxError as error:
        return [f"{module}:{error.lineno}: {error.msg}"]
    errors = []
    parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
    allowed = BOUNDARIES.get(module)

    def type_only(node: ast.AST) -> bool:
        while node in parents:
            node = parents[node]
            if isinstance(node, ast.If) and isinstance(node.test, ast.Name) and node.test.id == "TYPE_CHECKING":
                return True
        return False

    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            names = [item.name for item in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for name in names:
                if name == "src" or name.startswith("src."):
                    errors.append(f"{module}:{node.lineno}: use the installed canonical namespace, without src.")
                if allowed is not None and name not in allowed:
                    errors.append(f"{module}:{node.lineno}: forbidden dependency {name}")
                if module == "runtime/refresh_planning.py" and name == "model" and not type_only(node):
                    errors.append(f"{module}:{node.lineno}: model is a type-only dependency")
            if allowed is not None or module == "plugins/sports_dashboard/f1.py":
                if any(item.name == "*" for item in node.names):
                    errors.append(f"{module}:{node.lineno}: explicit imports required")
        if allowed is not None and isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.end_lineno - node.lineno + 1 > 80:
                errors.append(f"{module}:{node.lineno}: extracted functions are limited to 80 physical lines")
        if module == "refresh_task.py" and isinstance(node, ast.FunctionDef):
            if node.name == "_select_independent_refresh_command" and node.end_lineno - node.lineno + 1 > 480:
                errors.append(f"{module}:{node.lineno}: selection exceeds the reduced 480-line ceiling")
    if module == "refresh_task.py" and len(source.splitlines()) > 10040:
        errors.append(f"{module}: coordinator exceeds the reduced 10040-line ceiling; extract a responsibility")
    return errors


# TaskCancelled derives from RuntimeError, so broad handlers in plugins swallow
# cooperative cancellation and keep the single refresh worker busy past its
# deadline. Existing debt is ratcheted: guard new handlers with
# ``except TaskCancelled: raise`` (or re-raise) and lower this ceiling.
PLUGIN_CANCELLATION_SWALLOW_CEILING = 715
_CANCELLATION_TYPES = {"TaskCancelled", "TaskDeadlineExceeded"}
_CANCELLATION_CATCHERS = {"BaseException", "Exception", "RuntimeError"}


def _handler_names(handler: ast.ExceptHandler) -> set[str]:
    if handler.type is None:
        return {"BaseException"}
    nodes = handler.type.elts if isinstance(handler.type, ast.Tuple) else [handler.type]
    names = set()
    for node in nodes:
        if isinstance(node, ast.Name):
            names.add(node.id)
        elif isinstance(node, ast.Attribute):
            names.add(node.attr)
    return names


def _reraises(handler: ast.ExceptHandler) -> bool:
    for node in ast.walk(ast.Module(body=handler.body, type_ignores=[])):
        if isinstance(node, ast.Raise) and (
            node.exc is None
            or (isinstance(node.exc, ast.Name) and handler.name and node.exc.id == handler.name)
        ):
            return True
    return False


def cancellation_swallowing_handlers(source: str) -> int:
    """Count handlers that would silently absorb TaskCancelled."""
    count = 0
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Try):
            continue
        guarded = False
        for handler in node.handlers:
            names = _handler_names(handler)
            if names & _CANCELLATION_TYPES:
                guarded = True
            elif names & _CANCELLATION_CATCHERS and not guarded and not _reraises(handler):
                count += 1
    return count


def plugin_cancellation_swallowing(src_root: Path) -> int:
    total = 0
    for path in (src_root / "plugins").rglob("*.py"):
        relative = path.relative_to(src_root / "plugins").parts
        if relative[0] == "base_plugin":
            continue
        total += cancellation_swallowing_handlers(path.read_text(encoding="utf-8"))
    return total


def check_cancellation_ratchet(src_root: Path, ceiling: int = PLUGIN_CANCELLATION_SWALLOW_CEILING) -> list[str]:
    count = plugin_cancellation_swallowing(src_root)
    if count > ceiling:
        return [
            f"plugins: {count} broad handlers swallow TaskCancelled (ceiling {ceiling}); "
            "add `except TaskCancelled: raise` before new broad handlers"
        ]
    return []


def main() -> int:
    errors = []
    count = 0
    for directory in (PACKAGE / "src", PACKAGE / "tests", ROOT / "tools"):
        for path in directory.rglob("*.py"):
            # Do not traverse installed/generated dependencies if placed under tools.
            if any(part in {"node_modules", ".venv", "__pycache__"} for part in path.parts):
                continue
            count += 1
            errors.extend(check_source(path.read_text(encoding="utf-8"), path.relative_to(directory).as_posix()))
    errors.extend(check_cancellation_ratchet(PACKAGE / "src"))
    for error in errors:
        print(error)
    print(f"Architecture checks: {count} files, {len(errors)} violations")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
