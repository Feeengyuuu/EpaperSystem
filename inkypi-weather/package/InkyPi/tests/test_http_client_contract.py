import ast
from pathlib import Path


SRC_ROOT = Path(__file__).resolve().parents[1] / "src"
HTTP_CLIENT = (SRC_ROOT / "utils" / "http_client.py").resolve()


def _direct_requests_calls(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    violations = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        function = node.func
        if not isinstance(function, ast.Attribute):
            continue
        if isinstance(function.value, ast.Name) and function.value.id == "requests":
            if function.attr in {"get", "post", "put", "patch", "delete", "Session"}:
                violations.append((node.lineno, function.attr))
    return violations


def test_first_party_code_has_no_direct_requests_calls_or_private_sessions():
    violations = []
    for path in SRC_ROOT.rglob("*.py"):
        if path.resolve() == HTTP_CLIENT:
            continue
        for line, call in _direct_requests_calls(path):
            violations.append(f"{path.relative_to(SRC_ROOT)}:{line}: requests.{call}")

    assert violations == []
