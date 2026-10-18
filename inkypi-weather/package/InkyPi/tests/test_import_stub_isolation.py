"""Test modules must not plant fake application modules in sys.modules.

A fake ``plugins.base_plugin`` package installed at import time (with
``sys.modules.setdefault``) outlives its test module: when that module is
collected before the real package is imported, every later module that
imports ``plugins.base_plugin.*`` fails. Use ``monkeypatch.setitem`` (undone
after the test) or the real modules instead.
"""

import ast
from pathlib import Path

TESTS = Path(__file__).resolve().parent
SRC = TESTS.parent / "src"
APP_PACKAGES = frozenset(
    path.stem if path.is_file() else path.name
    for path in SRC.iterdir()
    if (path.is_dir() and (path / "__init__.py").exists()) or path.suffix == ".py"
)


def _sys_modules_writes(tree):
    """Yield (line, module name) for literal-key writes to sys.modules."""
    for node in ast.walk(tree):
        target = None
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            owner = node.func.value
            if (node.func.attr == "setdefault" and isinstance(owner, ast.Attribute)
                    and owner.attr == "modules" and getattr(owner.value, "id", None) == "sys" and node.args):
                target = node.args[0]
        elif isinstance(node, ast.Assign):
            for item in node.targets:
                if (isinstance(item, ast.Subscript) and isinstance(item.value, ast.Attribute)
                        and item.value.attr == "modules" and getattr(item.value.value, "id", None) == "sys"):
                    target = item.slice
        if isinstance(target, ast.Constant) and isinstance(target.value, str):
            yield node.lineno, target.value


def test_no_test_module_plants_fake_application_modules():
    offenders = []
    for path in sorted(TESTS.glob("test_*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for line, name in _sys_modules_writes(tree):
            if name.split(".")[0] in APP_PACKAGES:
                offenders.append(f"{path.name}:{line} {name}")
    assert offenders == []


def test_the_guard_recognises_the_leaking_pattern():
    leaking = ast.parse(
        "import sys, types\n"
        "sys.modules.setdefault('plugins.base_plugin', types.ModuleType('x'))\n"
        "sys.modules['utils.theme_utils'] = object()\n"
    )
    names = sorted(name for _line, name in _sys_modules_writes(leaking))
    assert names == ["plugins.base_plugin", "utils.theme_utils"]
    assert {"plugins", "utils"} <= APP_PACKAGES
