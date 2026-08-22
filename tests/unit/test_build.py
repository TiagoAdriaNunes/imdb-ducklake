import json
from io import StringIO
from types import SimpleNamespace

import pytest

from imdb_ducklake.application import build as build_module
from imdb_ducklake.application.build import build_lakehouse
from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import (
    AcquisitionError,
    IngestionError,
    PromotionError,
    TransformationError,
    ValidationError,
)
from imdb_ducklake.ingestion.pipeline import IngestionResult
from imdb_ducklake.ingestion.progress import StructuredLogCollector
from imdb_ducklake.lakehouse.validation import ValidationResult
from imdb_ducklake.observability import configure_logging, start_run_context
from imdb_ducklake.transformation.dbt_runner import DbtRunResult


class FakeDownloader:
    def __init__(self, *, failure=None) -> None:
        self.failure = failure
        self.calls = []

    def download_all(self, datasets, **kwargs):
        self.calls.append((tuple(datasets), kwargs))
        if self.failure is not None:
            raise self.failure
        entry = SimpleNamespace(size_bytes=10)
        return tuple(SimpleNamespace(manifest_entry=entry) for _dataset in datasets)


def _settings(tmp_path) -> Settings:
    repository = tmp_path / "repository"
    repository.mkdir()
    dbt = repository / "dbt"
    dbt.mkdir()
    (dbt / "dbt_project.yml").write_text("name: fixture", encoding="utf-8")
    (dbt / "profiles.yml").write_text("fixture: {}", encoding="utf-8")
    return Settings(repository_root=repository, data_dir=tmp_path / "data")


def _install_successful_stages(monkeypatch) -> None:
    def ingest(
        artifacts,
        *,
        build_paths,
        pipelines_dir,
        progress,
    ):
        assert isinstance(progress, StructuredLogCollector)
        assert progress.log_period == 10.0
        build_paths.catalog_path.write_text("catalog", encoding="utf-8")
        (build_paths.storage_dir / "rows.parquet").write_text("rows", encoding="utf-8")
        return IngestionResult(
            pipeline_name="fixture",
            dataset_name="raw",
            load_ids=("load-1",),
            catalog_path=build_paths.catalog_path,
            storage_dir=build_paths.storage_dir,
        )

    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(
        build_module,
        "run_dbt",
        lambda *_args, **_kwargs: DbtRunResult(("dbt", "build"), "passed", ""),
    )
    monkeypatch.setattr(
        build_module,
        "validate_build",
        lambda paths, **_kwargs: ValidationResult(paths.build_id, 31, {"mart": 1}),
    )
    monkeypatch.setattr(build_module, "checkpoint_lakehouse", lambda *_args, **_kwargs: None)


def test_build_runs_all_gates_and_promotes_only_after_validation(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    old_current = settings.current_dir
    old_current.mkdir(parents=True)
    (old_current / "marker.txt").write_text("old", encoding="utf-8")
    downloader = FakeDownloader()
    _install_successful_stages(monkeypatch)

    result = build_lakehouse(
        settings=settings,
        downloader=downloader,
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        force_download=True,
        reserve_bytes=0,
    )

    assert result.promoted.current_dir == settings.current_dir
    assert result.promoted.catalog_path.read_text(encoding="utf-8") == "catalog"
    assert not (settings.current_dir / "marker.txt").exists()
    assert result.promoted.previous_dir is not None
    assert (result.promoted.previous_dir / "marker.txt").read_text(encoding="utf-8") == "old"
    assert downloader.calls[0][1]["force"] is True
    assert not list((settings.lakehouse_dir / "builds").glob("*"))


def test_build_id_correlates_every_stage_including_acquisition(tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    downloader = FakeDownloader()
    _install_successful_stages(monkeypatch)
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)
    start_run_context("run-full-build")

    result = build_lakehouse(
        settings=settings,
        downloader=downloader,
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )

    events = [json.loads(line)["record"] for line in stream.getvalue().splitlines()]
    assert events
    stage_events = [event for event in events if "stage" in event["extra"]]
    assert stage_events
    for event in stage_events:
        assert event["extra"]["build_id"] == result.build_id
        assert event["extra"]["run_id"] == "run-full-build"
    event_codes = {event["extra"]["event_code"] for event in stage_events}
    assert "build_lock_waiting" in event_codes
    assert "acquisition_started" in event_codes
    assert "free_space_gate_passed" in event_codes


@pytest.mark.parametrize("stage", ["acquisition", "ingestion", "dbt", "validation", "promotion"])
def test_every_stage_failure_preserves_current_build(stage, tmp_path, monkeypatch) -> None:
    settings = _settings(tmp_path)
    settings.current_dir.mkdir(parents=True)
    marker = settings.current_dir / "marker.txt"
    marker.write_text("known-good", encoding="utf-8")
    downloader = FakeDownloader(
        failure=AcquisitionError("failed acquisition") if stage == "acquisition" else None
    )
    _install_successful_stages(monkeypatch)

    if stage == "ingestion":
        monkeypatch.setattr(
            build_module,
            "ingest_snapshot",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(IngestionError("failed ingestion")),
        )
    elif stage == "dbt":
        monkeypatch.setattr(
            build_module,
            "run_dbt",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(TransformationError("failed dbt")),
        )
    elif stage == "validation":
        monkeypatch.setattr(
            build_module,
            "validate_build",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(ValidationError("failed validation")),
        )
    elif stage == "promotion":
        monkeypatch.setattr(
            build_module,
            "promote_build",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(PromotionError("failed promotion")),
        )

    with pytest.raises(
        (AcquisitionError, IngestionError, TransformationError, ValidationError, PromotionError)
    ):
        build_lakehouse(
            settings=settings,
            downloader=downloader,
            dbt_executable="dbt",
            python_executable="python",
            environment={},
            reserve_bytes=0,
        )

    assert marker.read_text(encoding="utf-8") == "known-good"

    staged_builds = list((settings.lakehouse_dir / "builds").glob("*"))
    if stage in ("acquisition", "ingestion"):
        # An incomplete or failed raw load cannot be safely resumed, so these stages still
        # discard the whole build workspace.
        assert not staged_builds
    else:
        # A later failure must not discard the already-ingested raw build: retrying acquisition
        # and ingestion just to re-test a dbt/validation/promotion fix wastes real time when the
        # raw archives never changed. `make transform`/`make promote` can resume it directly.
        assert len(staged_builds) == 1
        assert (staged_builds[0] / "catalog.duckdb").read_text(encoding="utf-8") == "catalog"
        assert (staged_builds[0] / "storage" / "rows.parquet").read_text(encoding="utf-8") == "rows"
