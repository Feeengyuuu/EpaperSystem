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
    "plugins/sports_dashboard/f1_domain.py": {
        "__future__", "collections.abc", "datetime", "typing",
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
            if node.name == "_select_independent_refresh_command" and node.end_lineno - node.lineno + 1 > 650:
                errors.append(f"{module}:{node.lineno}: selection exceeds the reduced 650-line ceiling")
    if module == "refresh_task.py" and len(source.splitlines()) > 10260:
        errors.append(f"{module}: coordinator exceeds the reduced 10260-line ceiling; extract a responsibility")
    return errors


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
    for error in errors:
        print(error)
    print(f"Architecture checks: {count} files, {len(errors)} violations")
    return int(bool(errors))


if __name__ == "__main__":
    raise SystemExit(main())
