"""Executable dependency-boundary checks for the application package."""

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).parents[2] / "src" / "imdb_ducklake"

# Dependencies point inward toward shared policy and lifecycle types. Only the application
# orchestration and CLI composition roots may depend on every feature package.
ALLOWED_DEPENDENCIES = {
    "__init__": set(),
    "__main__": {"cli"},
    "acquisition": {"acquisition", "datasets", "exceptions"},
    "application": {
        "acquisition",
        "application",
        "config",
        "datasets",
        "exceptions",
        "ingestion",
        "lakehouse",
        "transformation",
    },
    "cli": {
        "acquisition",
        "application",
        "config",
        "datasets",
        "exceptions",
        "ingestion",
        "lakehouse",
        "observability",
        "transformation",
    },
    "config": {"exceptions"},
    "datasets": set(),
    "exceptions": set(),
    "ingestion": {"acquisition", "datasets", "exceptions", "ingestion", "lakehouse"},
    "lakehouse": {"exceptions", "lakehouse"},
    "observability": set(),
    "transformation": {"exceptions", "lakehouse", "transformation"},
}


def test_package_dependencies_follow_declared_boundaries() -> None:
    violations: list[str] = []

    for source_path in sorted(PACKAGE_ROOT.rglob("*.py")):
        relative = source_path.relative_to(PACKAGE_ROOT)
        owner = relative.parts[0] if len(relative.parts) > 1 else relative.stem
        allowed = ALLOWED_DEPENDENCIES[owner]
        tree = ast.parse(source_path.read_text(encoding="utf-8"), filename=str(source_path))

        for node in ast.walk(tree):
            imported = _internal_dependency(node)
            if imported is not None and imported not in allowed:
                violations.append(f"{relative}:{node.lineno} imports imdb_ducklake.{imported}")

    assert violations == [], "Dependency boundary violations:\n" + "\n".join(violations)


def _internal_dependency(node: ast.AST) -> str | None:
    if isinstance(node, ast.ImportFrom) and node.module:
        parts = node.module.split(".")
        if parts[0] == "imdb_ducklake" and len(parts) > 1:
            return parts[1]
    if isinstance(node, ast.Import):
        for alias in node.names:
            parts = alias.name.split(".")
            if parts[0] == "imdb_ducklake" and len(parts) > 1:
                return parts[1]
    return None
