"""W0 dependency rules derived from the current design."""

import ast
from pathlib import Path


PACKAGE_ROOT = Path(__file__).parents[1] / "src" / "conflux_weave"
DOMAIN_ROOTS = (PACKAGE_ROOT / "core", PACKAGE_ROOT / "evidence")
FORBIDDEN_FRAMEWORKS = {
    "chromadb",
    "deepeval",
    "fastapi",
    "langgraph",
    "openinference",
    "opentelemetry",
    "phoenix",
    "ragas",
    "uvicorn",
}
FORBIDDEN_W1_RUNTIME_DEPENDENCIES = FORBIDDEN_FRAMEWORKS | {
    "anthropic",
    "httpx",
    "openai",
    "requests",
}


def imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            modules.add(node.module)
    return modules


def test_package_does_not_import_legacy_conflux() -> None:
    violations: list[str] = []
    for path in PACKAGE_ROOT.rglob("*.py"):
        for module in imported_modules(path):
            if module == "conflux" or module.startswith("conflux."):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert violations == []


def test_domain_contracts_are_framework_independent() -> None:
    violations: list[str] = []
    for root in DOMAIN_ROOTS:
        for path in root.rglob("*.py"):
            for module in imported_modules(path):
                if module.split(".", 1)[0] in FORBIDDEN_FRAMEWORKS:
                    violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert violations == []


def test_core_only_imports_its_own_project_namespace() -> None:
    violations: list[str] = []
    for path in (PACKAGE_ROOT / "core").rglob("*.py"):
        for module in imported_modules(path):
            if module.startswith("conflux_weave.") and not module.startswith(
                "conflux_weave.core"
            ):
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert violations == []


def test_w1_runtime_shell_has_no_network_or_optional_framework_dependency() -> None:
    violations: list[str] = []
    paths = list((PACKAGE_ROOT / "runtime").rglob("*.py")) + [
        PACKAGE_ROOT / "cli.py",
        PACKAGE_ROOT / "search.py",
    ]
    for path in paths:
        for module in imported_modules(path):
            if module.split(".", 1)[0] in FORBIDDEN_W1_RUNTIME_DEPENDENCIES:
                violations.append(f"{path.relative_to(PACKAGE_ROOT)} -> {module}")
    assert violations == []


def test_w5_application_contracts_remain_framework_independent() -> None:
    api_contracts = PACKAGE_ROOT / "api_contracts.py"
    modules = imported_modules(api_contracts)

    assert "fastapi" not in modules
    assert "uvicorn" not in modules
    assert not (PACKAGE_ROOT / "asgi.py").exists()


def test_w5_server_uses_one_lifespan_worker_and_one_packaged_workbench() -> None:
    server = (PACKAGE_ROOT / "server.py").read_text(encoding="utf-8")
    cli = (PACKAGE_ROOT / "cli.py").read_text(encoding="utf-8")

    assert "lifespan" in server
    assert "WorkerLoop" in server
    assert "workers=1" in cli
    assert "EventSourceResponse" not in server
    workbench = PACKAGE_ROOT / "workbench"
    assert sorted(path.name for path in workbench.iterdir()) == [
        "app.js",
        "index.html",
        "styles.css",
    ]
    assert "StaticFiles" in server


def test_w5_4_smoke_is_local_and_has_no_new_runtime_dependency() -> None:
    smoke = (PACKAGE_ROOT / "offline_smoke.py").read_text(encoding="utf-8")
    cli = (PACKAGE_ROOT / "cli.py").read_text(encoding="utf-8")
    assert "offline-smoke" in cli
    assert "network_calls" in smoke
    assert "provider_calls" in smoke
    assert "urlopen" not in smoke
    assert "requests" not in smoke
