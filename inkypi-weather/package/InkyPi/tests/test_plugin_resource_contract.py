import ast
import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
PLUGIN_ROOT = PROJECT_ROOT / "src" / "plugins"
REQUEST_METHODS = {"get", "post", "put", "patch", "delete", "request", "Session"}
CHROMIUM_TOKENS = {"chromium", "chrome", "headless-shell"}
CACHE_SUFFIXES = ("_IMAGE_CACHE", "_LOGO_CACHE")
MAX_IMAGE_CACHE_ENTRIES = 128
MAX_IMAGE_CACHE_BYTES = 20 * 1024 * 1024


def _qualified_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _qualified_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


def _assigned_names(node):
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, (ast.Tuple, ast.List)):
        names = set()
        for item in node.elts:
            names.update(_assigned_names(item))
        return names
    return set()


def _contains_name(node, names):
    return any(
        isinstance(candidate, ast.Name) and candidate.id in names
        for candidate in ast.walk(node)
    )


def _contains_response_body(node):
    return any(
        isinstance(candidate, ast.Attribute)
        and candidate.attr in {"content", "raw"}
        for candidate in ast.walk(node)
    )


def _string_literals(node):
    return [
        candidate.value.lower()
        for candidate in ast.walk(node)
        if isinstance(candidate, ast.Constant) and isinstance(candidate.value, str)
    ]


def _import_aliases(tree):
    request_modules = {"requests"}
    request_calls = set()
    popen_calls = {"subprocess.Popen"}
    image_open_calls = {"Image.open", "PIL.Image.open"}
    for node in tree.body:
        if isinstance(node, ast.Import):
            for alias in node.names:
                local = alias.asname or alias.name.split(".")[0]
                if alias.name == "requests" or alias.name.startswith("requests."):
                    request_modules.add(local)
                if alias.name == "subprocess":
                    popen_calls.add(f"{local}.Popen")
                if alias.name in {"PIL.Image", "PIL"}:
                    image_open_calls.add(f"{local}.open")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for alias in node.names:
                local = alias.asname or alias.name
                if module == "requests" or module.startswith("requests."):
                    if alias.name in REQUEST_METHODS:
                        request_calls.add(local)
                if module == "subprocess" and alias.name == "Popen":
                    popen_calls.add(local)
                if module in {"PIL.Image", "PIL"} and alias.name == "open":
                    image_open_calls.add(local)
                if module == "PIL" and alias.name == "Image":
                    image_open_calls.add(f"{local}.open")
    return request_modules, request_calls, popen_calls, image_open_calls


def _numeric_constant(node):
    if isinstance(node, ast.Constant) and type(node.value) in {int, float}:
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, (ast.UAdd, ast.USub)):
        value = _numeric_constant(node.operand)
        if value is None:
            return None
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _numeric_constant(node.left)
        right = _numeric_constant(node.right)
        if left is None or right is None:
            return None
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
    return None


def _function_tainted_names(function):
    assignments = [
        node
        for node in ast.walk(function)
        if isinstance(node, (ast.Assign, ast.AnnAssign, ast.NamedExpr))
    ]
    tainted = set()
    changed = True
    while changed:
        changed = False
        for assignment in assignments:
            if isinstance(assignment, ast.Assign):
                targets = assignment.targets
                value = assignment.value
            else:
                targets = [assignment.target]
                value = assignment.value
            if value is None:
                continue
            if not _contains_response_body(value) and not _contains_name(value, tainted):
                continue
            for target in targets:
                for name in _assigned_names(target):
                    if name not in tainted:
                        tainted.add(name)
                        changed = True
    return tainted


def _resource_violations(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    request_modules, request_calls, popen_calls, image_open_calls = _import_aliases(tree)
    violations = []

    for node in tree.body:
        if not isinstance(node, (ast.Assign, ast.AnnAssign)):
            continue
        targets = node.targets if isinstance(node, ast.Assign) else [node.target]
        value = node.value
        for target in targets:
            for name in _assigned_names(target):
                if not name.endswith(CACHE_SUFFIXES):
                    continue
                initializer = _qualified_name(value.func) if isinstance(value, ast.Call) else ""
                if not initializer.endswith("ImageLRUCache"):
                    violations.append((node.lineno, "unbounded_module_cache", name))
                    continue
                keywords = {
                    keyword.arg: _numeric_constant(keyword.value)
                    for keyword in value.keywords
                    if keyword.arg in {"max_entries", "max_bytes"}
                }
                entries = keywords.get("max_entries", MAX_IMAGE_CACHE_ENTRIES)
                byte_limit = keywords.get("max_bytes", MAX_IMAGE_CACHE_BYTES)
                if (
                    entries is None
                    or byte_limit is None
                    or not 0 < entries <= MAX_IMAGE_CACHE_ENTRIES
                    or not 0 < byte_limit <= MAX_IMAGE_CACHE_BYTES
                ):
                    violations.append((node.lineno, "over_budget_module_cache", name))

    functions = [
        node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda))
    ]
    function_by_call = {}
    for function in functions:
        for candidate in ast.walk(function):
            if isinstance(candidate, ast.Call):
                function_by_call[id(candidate)] = function

    tainted_by_function = {
        id(function): _function_tainted_names(function)
        for function in functions
    }
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        name = _qualified_name(node.func)
        request_module_call = any(
            name.startswith(f"{module}.")
            and name.rsplit(".", 1)[-1] in REQUEST_METHODS
            for module in request_modules
        )
        if request_module_call or name in request_calls:
            violations.append((node.lineno, "direct_requests", name))

        if name in image_open_calls and node.args:
            function = function_by_call.get(id(node))
            tainted = tainted_by_function.get(id(function), set())
            if _contains_response_body(node.args[0]) or _contains_name(node.args[0], tainted):
                violations.append(
                    (node.lineno, "unsafe_network_image_decode", name)
                )

        if name in popen_calls:
            function = function_by_call.get(id(node), node)
            literals = _string_literals(function)
            if any(token in literal for literal in literals for token in CHROMIUM_TOKENS):
                violations.append((node.lineno, "private_chromium", name))

    return violations


def test_resource_scanner_detects_each_forbidden_pattern(tmp_path):
    source = tmp_path / "bad_plugin.py"
    source.write_text(
        """
import requests as rq
from requests import post as send
from subprocess import Popen as Launch
from PIL import Image
from io import BytesIO

BAD_IMAGE_CACHE = {}

def render():
    response = rq.get("https://example.test/image")
    payload = response.content
    Image.open(BytesIO(payload))
    send("https://example.test/write")
    Launch(["chromium", "--headless"])
""",
        encoding="utf-8",
    )

    codes = {code for _line, code, _detail in _resource_violations(source)}

    assert codes == {
        "direct_requests",
        "unsafe_network_image_decode",
        "private_chromium",
        "unbounded_module_cache",
    }


def test_image_lru_cache_wrapper_is_an_approved_module_cache(tmp_path):
    source = tmp_path / "bounded_plugin.py"
    source.write_text(
        "from utils.cache_manager import ImageLRUCache\n"
        "TEAM_LOGO_CACHE = ImageLRUCache(max_entries=128, max_bytes=1024)\n",
        encoding="utf-8",
    )

    assert _resource_violations(source) == []


def test_image_lru_cache_wrapper_cannot_exceed_the_runtime_budget(tmp_path):
    source = tmp_path / "oversize_plugin.py"
    source.write_text(
        "from utils.cache_manager import ImageLRUCache\n"
        "TEAM_LOGO_CACHE = ImageLRUCache(max_entries=129, max_bytes=21 * 1024 * 1024)\n",
        encoding="utf-8",
    )

    assert {
        code for _line, code, _detail in _resource_violations(source)
    } == {"over_budget_module_cache"}


def test_all_builtin_plugins_follow_the_resource_contract():
    violations = []
    for path in sorted(PLUGIN_ROOT.rglob("*.py")):
        for line, code, detail in _resource_violations(path):
            relative = path.relative_to(PROJECT_ROOT)
            violations.append(f"{relative}:{line}: {code}: {detail}")

    assert violations == [], "\n".join(violations)


def test_all_builtin_plugin_manifests_remain_schema_v2():
    violations = []
    manifests = sorted(PLUGIN_ROOT.glob("*/plugin-info.json"))
    assert manifests
    for path in manifests:
        payload = json.loads(path.read_text(encoding="utf-8"))
        schema = payload.get("schema_version")
        capabilities = payload.get("capabilities")
        if isinstance(schema, bool) or schema != 2:
            violations.append(f"{path.parent.name}: schema_version={schema!r}")
        if not isinstance(capabilities, dict):
            violations.append(f"{path.parent.name}: capabilities must be an object")

    assert violations == [], "\n".join(violations)
