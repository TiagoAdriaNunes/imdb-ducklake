"""Explicit subprocess boundary for dbt commands against one DuckLake build."""

from __future__ import annotations

import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from imdb_ducklake.exceptions import TransformationError
from imdb_ducklake.lakehouse.lifecycle import BuildPaths

ProcessRunner = Callable[
    [Sequence[str], Path, Mapping[str, str]],
    subprocess.CompletedProcess[str],
]


@dataclass(frozen=True, slots=True)
class DbtRunResult:
    """Application-facing result of one successful dbt invocation."""

    command: tuple[str, ...]
    stdout: str
    stderr: str


def run_dbt(
    dbt_args: Sequence[str],
    *,
    build_paths: BuildPaths,
    project_dir: Path,
    profiles_dir: Path,
    controller_path: Path,
    executable: str,
    environment: Mapping[str, str],
    runner: ProcessRunner | None = None,
) -> DbtRunResult:
    """Run dbt with explicit paths and environment against the given DuckLake catalog."""
    if not dbt_args:
        raise ValueError("At least one dbt argument is required")
    resolved_project = project_dir.resolve()
    resolved_profiles = profiles_dir.resolve()
    resolved_controller = controller_path.resolve()
    _validate_inputs(build_paths, resolved_project, resolved_profiles)
    try:
        resolved_controller.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise TransformationError(
            f"Could not create dbt controller directory {resolved_controller.parent}"
        ) from error

    command = (
        executable,
        *dbt_args,
        "--project-dir",
        str(resolved_project),
        "--profiles-dir",
        str(resolved_profiles),
        "--no-use-colors",
    )
    process_environment = dict(environment)
    process_environment.update(
        {
            "IMDB_DUCKLAKE_CATALOG": build_paths.catalog_path.as_posix(),
            "IMDB_DUCKLAKE_STORAGE": build_paths.storage_dir.as_posix(),
            "IMDB_DUCKLAKE_DBT_CONTROLLER": resolved_controller.as_posix(),
        }
    )
    try:
        completed = (runner or _run_process)(command, resolved_project.parent, process_environment)
    except OSError as error:
        raise TransformationError(f"Could not start dbt executable {executable!r}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 2_000:
            detail = detail[-2_000:]
        raise TransformationError(
            f"dbt {' '.join(dbt_args)} failed with exit code {completed.returncode}: {detail}"
        )
    return DbtRunResult(command, completed.stdout, completed.stderr)


def _run_process(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _validate_inputs(build_paths: BuildPaths, project_dir: Path, profiles_dir: Path) -> None:
    if not build_paths.catalog_path.is_file() or not build_paths.storage_dir.is_dir():
        raise TransformationError(f"DuckLake build is incomplete: {build_paths.temporary_dir}")
    if not (project_dir / "dbt_project.yml").is_file():
        raise TransformationError(f"dbt project metadata does not exist in {project_dir}")
    if not (profiles_dir / "profiles.yml").is_file():
        raise TransformationError(f"dbt profiles metadata does not exist in {profiles_dir}")
