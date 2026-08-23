import os
import subprocess
import sys
from io import StringIO

import pytest

from imdb_ducklake.exceptions import TransformationError
from imdb_ducklake.lakehouse.catalog import CatalogTarget
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build
from imdb_ducklake.observability import configure_logging, start_run_context
from imdb_ducklake.transformation.dbt_runner import run_dbt


def _inputs(tmp_path):
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="dbt-unit")
    initialize_build(paths)
    paths.catalog_path.write_text("catalog", encoding="utf-8")
    project = tmp_path / "dbt"
    project.mkdir()
    (project / "dbt_project.yml").write_text("name: fixture", encoding="utf-8")
    (project / "profiles.yml").write_text("fixture: {}", encoding="utf-8")
    return paths, project


def test_runs_dbt_with_explicit_paths_and_environment(tmp_path) -> None:
    paths, project = _inputs(tmp_path)
    received = None

    def runner(command, cwd, environment):
        nonlocal received
        received = command, cwd, environment
        return subprocess.CompletedProcess(command, 0, "dbt succeeded", "")

    controller_path = tmp_path / "state" / "controller.duckdb"
    result = run_dbt(
        ("build", "--select", "staging"),
        build_paths=paths,
        project_dir=project,
        profiles_dir=project,
        controller_path=controller_path,
        executable="dbt",
        environment={"EXISTING": "value"},
        runner=runner,
    )

    assert result.stdout == "dbt succeeded"
    assert received is not None
    command, cwd, environment = received
    assert command[0:4] == ("dbt", "build", "--select", "staging")
    assert cwd == tmp_path
    assert environment["EXISTING"] == "value"
    assert environment["IMDB_DUCKLAKE_CATALOG"] == paths.catalog_path.as_posix()
    assert environment["IMDB_DUCKLAKE_STORAGE"] == paths.storage_dir.as_posix()
    assert environment["IMDB_DUCKLAKE_DBT_CONTROLLER"] == controller_path.resolve().as_posix()


def test_runs_dbt_against_postgresql_catalog_target(tmp_path) -> None:
    paths, project = _inputs(tmp_path)
    target = CatalogTarget(
        "postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        tmp_path / "shared-storage",
    )
    target.storage_dir.mkdir()
    received = None

    def runner(command, cwd, environment):
        nonlocal received
        received = environment
        return subprocess.CompletedProcess(command, 0, "passed", "")

    run_dbt(
        ("build",),
        build_paths=paths,
        project_dir=project,
        profiles_dir=project,
        controller_path=tmp_path / "state" / "controller.duckdb",
        executable="dbt",
        environment={},
        runner=runner,
        catalog_target=target,
    )

    assert received is not None
    assert received["IMDB_DUCKLAKE_CATALOG"].startswith("postgres:dbname=")
    assert received["IMDB_DUCKLAKE_STORAGE"] == target.storage_dir.as_posix()
    assert received["IMDB_DUCKLAKE_METADATA_SCHEMA"] == "imdb_lake"


def test_rejects_incomplete_build_and_wraps_dbt_failure(tmp_path) -> None:
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="dbt-unit")
    with pytest.raises(TransformationError, match="build is incomplete"):
        run_dbt(
            ("build",),
            build_paths=paths,
            project_dir=tmp_path,
            profiles_dir=tmp_path,
            controller_path=tmp_path / "controller.duckdb",
            executable="dbt",
            environment={},
        )

    paths, project = _inputs(tmp_path / "failure")

    def fail(command, _cwd, _environment):
        return subprocess.CompletedProcess(command, 2, "", "SQL failed")

    with pytest.raises(TransformationError, match="exit code 2: SQL failed"):
        run_dbt(
            ("build",),
            build_paths=paths,
            project_dir=project,
            profiles_dir=project,
            controller_path=tmp_path / "controller.duckdb",
            executable="dbt",
            environment={},
            runner=fail,
        )


def test_streams_dbt_stdout_and_stderr_while_retaining_output(tmp_path) -> None:
    paths, project = _inputs(tmp_path)
    events: list[tuple[str, str]] = []

    result = run_dbt(
        (
            "-c",
            "import sys; print('model started', flush=True); "
            "print('model warning', file=sys.stderr, flush=True)",
        ),
        build_paths=paths,
        project_dir=project,
        profiles_dir=project,
        controller_path=tmp_path / "controller.duckdb",
        executable=sys.executable,
        environment=os.environ,
        output_handler=lambda stream, line: events.append((stream, line)),
    )

    assert result.stdout == "model started\n"
    assert result.stderr == "model warning\n"
    assert ("stdout", "model started") in events
    assert ("stderr", "model warning") in events


def test_default_output_handler_removes_dbt_noise_from_console(tmp_path) -> None:
    paths, project = _inputs(tmp_path)
    stream = StringIO()
    configure_logging("INFO", "console", stream=stream)
    start_run_context("run-dbt-console")

    run_dbt(
        (
            "-c",
            "print('05:12:41  ', flush=True); "
            "print('05:12:42  1 of 1 PASS model [PASS in 0.01s]', flush=True)",
        ),
        build_paths=paths,
        project_dir=project,
        profiles_dir=project,
        controller_path=tmp_path / "controller.duckdb",
        executable=sys.executable,
        environment=os.environ,
    )

    output = stream.getvalue()
    assert "dbt | 1 of 1 PASS model [PASS in 0.01s]" in output
    assert "dbt |  |" not in output
    assert "dbt_stream=" not in output
    assert "dbt_message=" not in output


def test_environment_secrets_never_reach_logged_output(tmp_path) -> None:
    paths, project = _inputs(tmp_path)
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    start_run_context("run-dbt-secrets")

    run_dbt(
        ("-c", "print('model started', flush=True)"),
        build_paths=paths,
        project_dir=project,
        profiles_dir=project,
        controller_path=tmp_path / "controller.duckdb",
        executable=sys.executable,
        environment={**os.environ, "IMDB_LAKEHOUSE_SECRET_TOKEN": "s3cr3t-value"},
    )

    output = stream.getvalue()
    assert "s3cr3t-value" not in output
    assert "IMDB_LAKEHOUSE_SECRET_TOKEN" not in output
