import json
import subprocess
from pathlib import Path

import pytest

from imdb_ducklake.exceptions import ValidationError
from imdb_ducklake.lakehouse import validation as validation_module
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build
from imdb_ducklake.lakehouse.validation import (
    REQUIRED_RELATIONS,
    ValidationResult,
    validate_build,
)


def _complete_paths(tmp_path: Path) -> BuildPaths:
    paths = BuildPaths.create(tmp_path / "ducklake", build_id="validation-unit")
    initialize_build(paths)
    paths.catalog_path.write_text("catalog", encoding="utf-8")
    return paths


def test_runs_validation_in_a_separate_python_process(tmp_path) -> None:
    paths = _complete_paths(tmp_path)
    received = None
    payload = {
        "build_id": paths.build_id,
        "relation_count": sum(len(relations) for relations in REQUIRED_RELATIONS.values()),
        "mart_row_counts": {"mart_title_search": 3},
    }

    def runner(command, cwd, environment):
        nonlocal received
        received = command, cwd, environment
        return subprocess.CompletedProcess(command, 0, json.dumps(payload), "")

    result = validate_build(
        paths,
        executable="python",
        environment={"EXISTING": "value"},
        working_directory=tmp_path,
        runner=runner,
    )

    assert result.build_id == paths.build_id
    assert result.mart_row_counts == {"mart_title_search": 3}
    assert received is not None
    command, cwd, environment = received
    assert command[0:3] == ("python", "-m", "imdb_ducklake.lakehouse.validation")
    assert "--catalog" in command
    assert cwd == tmp_path
    assert environment["EXISTING"] == "value"


def test_rejects_incomplete_build_and_failed_or_invalid_worker_output(tmp_path) -> None:
    incomplete = BuildPaths.create(tmp_path / "missing", build_id="validation-unit")
    with pytest.raises(ValidationError, match="build is incomplete"):
        validate_build(
            incomplete,
            executable="python",
            environment={},
            working_directory=tmp_path,
        )

    paths = _complete_paths(tmp_path / "complete")

    def fail(command, _cwd, _environment):
        return subprocess.CompletedProcess(command, 1, "", "missing relation")

    with pytest.raises(ValidationError, match="exit code 1: missing relation"):
        validate_build(
            paths,
            executable="python",
            environment={},
            working_directory=tmp_path,
            runner=fail,
        )

    def invalid(command, _cwd, _environment):
        return subprocess.CompletedProcess(command, 0, "not-json", "")

    with pytest.raises(ValidationError, match="invalid result"):
        validate_build(
            paths,
            executable="python",
            environment={},
            working_directory=tmp_path,
            runner=invalid,
        )


def test_worker_serializes_success_and_reports_validation_failure(
    tmp_path, monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        validation_module,
        "_validate_read_only",
        lambda *_args: ValidationResult("worker-build", 31, {"mart": 2}),
    )

    exit_code = validation_module._worker_main(
        [
            "--catalog",
            str(tmp_path / "catalog.duckdb"),
            "--storage",
            str(tmp_path / "storage"),
            "--build-id",
            "worker-build",
        ]
    )

    assert exit_code == 0
    assert json.loads(capsys.readouterr().out)["relation_count"] == 31

    def fail(*_args):
        raise ValidationError("read-only attach failed")

    monkeypatch.setattr(validation_module, "_validate_read_only", fail)
    exit_code = validation_module._worker_main(
        [
            "--catalog",
            str(tmp_path / "catalog.duckdb"),
            "--storage",
            str(tmp_path / "storage"),
            "--build-id",
            "worker-build",
        ]
    )

    assert exit_code == 1
    assert "read-only attach failed" in capsys.readouterr().err
