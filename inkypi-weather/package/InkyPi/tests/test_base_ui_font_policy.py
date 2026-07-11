import ast
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]

CSS_BASE_UI_FILES = (
    "src/plugins/ai_text/render/ai_text.css",
    "src/plugins/calendar/render/calendar.css",
    "src/plugins/countdown/render/countdown.css",
    "src/plugins/github/render/github.css",
    "src/plugins/mini_weather/render/mini_weather.css",
    "src/plugins/rss/render/rss.css",
    "src/plugins/todo_list/render/todo_list.css",
    "src/plugins/weather/render/weather.css",
    "src/plugins/year_progress/render/year_progress.css",
)

PYTHON_BASE_UI_BYPASS_FILES = (
    "src/plugins/comic/comic.py",
    "src/plugins/flow_progress/flow_progress.py",
    "src/plugins/simple_calendar/simple_calendar.py",
    "src/plugins/mini_weather/mini_weather.py",
    "src/plugins/github/github_contributions.py",
    "src/plugins/gcd_comic_covers/gcd_comic_covers.py",
    "src/plugins/magazine_covers/magazine_covers.py",
)

DECORATIVE_FONT_ALLOWLIST = ("dogica", "ds-digital", "napoli")
BASE_UI_FONT_STACK = '"Microsoft YaHei", "\u5fae\u8f6f\u96c5\u9ed1", Arial, sans-serif'


def test_base_ui_font_policy_css_uses_shared_stack_or_decorative_allowlist():
    offenders = []
    declaration = re.compile(r"font-family\s*:\s*([^;]+)", re.IGNORECASE)

    for relative_path in CSS_BASE_UI_FILES:
        path = PROJECT_ROOT / relative_path
        content = path.read_text(encoding="utf-8")
        shared_stack_count = 0
        for match in declaration.finditer(content):
            value = match.group(1).strip()
            lowered = value.casefold()
            decorative = any(
                allowed in lowered for allowed in DECORATIVE_FONT_ALLOWLIST
            )
            if value == BASE_UI_FONT_STACK:
                shared_stack_count += 1
            if "jost" in lowered or (not decorative and value != BASE_UI_FONT_STACK):
                line = content[: match.start()].count("\n") + 1
                offenders.append(f"{relative_path}:{line}: {value}")
        if shared_stack_count == 0:
            offenders.append(f"{relative_path}: missing shared YaHei stack")

    assert offenders == []


def test_base_ui_font_policy_python_has_no_ordinary_jost_calls():
    offenders = []

    for relative_path in PYTHON_BASE_UI_BYPASS_FILES:
        path = PROJECT_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        get_font_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "utils.app_utils"
            for alias in node.names
            if alias.name == "get_font"
        }
        app_utils_names = {
            alias.asname
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "utils.app_utils" and alias.asname
        }
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            is_get_font = (
                isinstance(node.func, ast.Name)
                and node.func.id in get_font_names
            ) or (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "get_font"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id in app_utils_names
            )
            family = node.args[0] if node.args else next(
                (
                    keyword.value
                    for keyword in node.keywords
                    if keyword.arg == "font_name"
                ),
                None,
            )
            if (
                is_get_font
                and isinstance(family, ast.Constant)
                and family.value == "Jost"
            ):
                offenders.append(f"{relative_path}:{node.lineno}")

    assert offenders == []


def test_base_ui_font_policy_python_bypasses_use_shared_resolver():
    offenders = []

    for relative_path in PYTHON_BASE_UI_BYPASS_FILES:
        path = PROJECT_ROOT / relative_path
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        imported_names = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom)
            and node.module == "utils.app_utils"
            for alias in node.names
            if alias.name == "get_base_ui_font"
        }
        module_aliases = {
            alias.asname or alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.Import)
            for alias in node.names
            if alias.name == "utils.app_utils"
        }
        has_call = any(
            isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id in imported_names
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get_base_ui_font"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in module_aliases
                )
            )
            for node in ast.walk(tree)
        )
        if not imported_names and not module_aliases:
            offenders.append(f"{relative_path}: missing resolver import")
        elif not has_call:
            offenders.append(f"{relative_path}: missing resolver call")

    assert offenders == []
