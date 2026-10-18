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
    LifecycleError,
    PromotionError,
    TransformationError,
    ValidationError,
)
from imdb_ducklake.ingestion.pipeline import IngestionResult
from imdb_ducklake.ingestion.progress import StructuredLogCollector
from imdb_ducklake.lakehouse import lifecycle
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
        workers,
        chunk_size,
        catalog_target=None,
    ):
        assert isinstance(progress, StructuredLogCollector)
        assert progress.log_period == 10.0
        assert workers >= 1
        assert chunk_size >= 1
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


def test_postgresql_build_stages_writes_and_promotes_to_current(tmp_path, monkeypatch) -> None:
    """A PostgreSQL-backed build has no local catalog.duckdb (metadata lives in PostgreSQL), but
    it must still stage every write into an isolated builds/<id>/storage and only reach
    current/storage through the same atomic promote_build local builds use - never write directly
    into a permanent shared directory (ADR 0010's follow-up amendment)."""
    base = _settings(tmp_path)
    settings = Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    downloader = FakeDownloader()
    calls: dict[str, object] = {}

    def ingest(
        artifacts, *, build_paths, pipelines_dir, progress, workers, chunk_size, catalog_target
    ):
        calls["ingestion_target"] = catalog_target
        assert catalog_target.storage_dir == build_paths.storage_dir
        (catalog_target.storage_dir / "raw.parquet").write_text("raw", encoding="utf-8")
        return IngestionResult(
            "fixture", "raw", ("load-1",), catalog_target.url, catalog_target.storage_dir
        )

    def transform(*_args, catalog_target, **_kwargs):
        calls["dbt_target"] = catalog_target
        return DbtRunResult(("dbt", "build"), "passed", "")

    def validate(paths, *, catalog_target, **_kwargs):
        calls["validation_target"] = catalog_target
        return ValidationResult(paths.build_id, 31, {"mart": 1})

    checkpoint_calls: list = []
    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(build_module, "run_dbt", transform)
    monkeypatch.setattr(build_module, "validate_build", validate)
    monkeypatch.setattr(
        build_module,
        "checkpoint_lakehouse",
        lambda *_a, **_kw: pytest.fail("local checkpoint must not run"),
    )
    monkeypatch.setattr(
        build_module,
        "checkpoint_catalog_target",
        lambda target: checkpoint_calls.append(target) or (),
    )

    result = build_lakehouse(
        settings=settings,
        downloader=downloader,
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )

    staged_storage_dir = calls["ingestion_target"].storage_dir
    assert calls["ingestion_target"] == calls["dbt_target"] == calls["validation_target"]
    assert staged_storage_dir == settings.lakehouse_dir / "builds" / result.build_id / "storage"

    # Promotion moved the staged directory into current/storage; nothing was ever written
    # directly into a shared, permanent location.
    assert result.promoted.storage_dir == settings.current_dir / "storage"
    assert (result.promoted.storage_dir / "raw.parquet").read_text(encoding="utf-8") == "raw"
    assert not list((settings.lakehouse_dir / "builds").glob("*"))
    assert not list(settings.data_dir.rglob("catalog.duckdb"))

    assert len(checkpoint_calls) == 1
    assert checkpoint_calls[0].url == settings.catalog_url
    assert checkpoint_calls[0].storage_dir == result.promoted.storage_dir


def test_postgresql_build_seeds_raw_storage_from_current(tmp_path, monkeypatch) -> None:
    """dlt's own bookkeeping tables (_dlt_loads, etc.) are never dropped between runs and the
    Postgres catalog already has committed history for them once any build has been promoted, so
    a fresh isolated builds/<id>/storage must be seeded with the promoted build's raw/ Parquet
    first - otherwise dlt's sync step fails trying to open a file that only exists under
    current/storage (see the ADR 0012 gap this seeding fixes)."""
    base = _settings(tmp_path)
    settings = Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    promoted_raw = settings.current_dir / "storage" / "raw"
    promoted_raw.mkdir(parents=True)
    (promoted_raw / "_dlt_loads.parquet").write_text("history", encoding="utf-8")
    downloader = FakeDownloader()
    seeded_before_ingest: dict[str, str] = {}

    def ingest(
        artifacts, *, build_paths, pipelines_dir, progress, workers, chunk_size, catalog_target
    ):
        seeded_before_ingest["content"] = (
            build_paths.storage_dir / "raw" / "_dlt_loads.parquet"
        ).read_text(encoding="utf-8")
        return IngestionResult(
            "fixture", "raw", ("load-1",), catalog_target.url, catalog_target.storage_dir
        )

    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(
        build_module, "run_dbt", lambda *_a, **_kw: DbtRunResult(("dbt", "build"), "passed", "")
    )
    monkeypatch.setattr(
        build_module,
        "validate_build",
        lambda paths, **_kwargs: ValidationResult(paths.build_id, 31, {"mart": 1}),
    )
    monkeypatch.setattr(build_module, "checkpoint_catalog_target", lambda *_a, **_kw: ())

    build_lakehouse(
        settings=settings,
        downloader=downloader,
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )

    assert seeded_before_ingest["content"] == "history"


def test_postgresql_first_build_has_no_raw_storage_to_seed(tmp_path, monkeypatch) -> None:
    """The very first Postgres-backed build has no promoted current/storage yet - seeding must be
    a no-op, not an error."""
    base = _settings(tmp_path)
    settings = Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    assert not settings.current_dir.exists()
    downloader = FakeDownloader()

    def ingest(
        artifacts, *, build_paths, pipelines_dir, progress, workers, chunk_size, catalog_target
    ):
        assert not (build_paths.storage_dir / "raw").exists()
        return IngestionResult(
            "fixture", "raw", ("load-1",), catalog_target.url, catalog_target.storage_dir
        )

    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(
        build_module, "run_dbt", lambda *_a, **_kw: DbtRunResult(("dbt", "build"), "passed", "")
    )
    monkeypatch.setattr(
        build_module,
        "validate_build",
        lambda paths, **_kwargs: ValidationResult(paths.build_id, 31, {"mart": 1}),
    )
    monkeypatch.setattr(build_module, "checkpoint_catalog_target", lambda *_a, **_kw: ())

    build_lakehouse(
        settings=settings,
        downloader=downloader,
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )


def _postgresql_settings(tmp_path) -> Settings:
    base = _settings(tmp_path)
    return Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )


def _install_postgresql_stages(monkeypatch, *, on_ingest=None, on_checkpoint=None) -> None:
    def ingest(
        artifacts, *, build_paths, pipelines_dir, progress, workers, chunk_size, catalog_target
    ):
        if on_ingest is not None:
            on_ingest()
        (catalog_target.storage_dir / "raw.parquet").write_text("raw", encoding="utf-8")
        return IngestionResult(
            "fixture", "raw", ("load-1",), catalog_target.url, catalog_target.storage_dir
        )

    def checkpoint(*_args, **_kwargs):
        if on_checkpoint is not None:
            on_checkpoint()
        return ()

    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(
        build_module, "run_dbt", lambda *_a, **_kw: DbtRunResult(("dbt", "build"), "passed", "")
    )
    monkeypatch.setattr(
        build_module,
        "validate_build",
        lambda paths, **_kwargs: ValidationResult(paths.build_id, 31, {"mart": 1}),
    )
    monkeypatch.setattr(build_module, "checkpoint_catalog_target", checkpoint)


def test_postgresql_build_prunes_only_after_promotion_and_checkpoint(tmp_path, monkeypatch) -> None:
    """Stale builds survive ingest and checkpoint, then are removed with retired/."""
    settings = _postgresql_settings(tmp_path)
    stale = settings.lakehouse_dir / "builds" / "20260101T000000Z-stale0000001"
    (stale / "storage").mkdir(parents=True)
    (stale / "storage" / "marker.txt").write_text("needed until checkpoint", encoding="utf-8")
    old_retired = settings.lakehouse_dir / "retired" / "20260101T000000Z-old000000001-previous-x"
    old_retired.mkdir(parents=True)
    (settings.current_dir / "storage").mkdir(parents=True)
    seen: list[str] = []

    def still_staged(stage: str):
        def check() -> None:
            assert (stale / "storage" / "marker.txt").is_file()
            seen.append(stage)

        return check

    _install_postgresql_stages(
        monkeypatch,
        on_ingest=still_staged("ingest"),
        on_checkpoint=still_staged("checkpoint"),
    )

    result = build_lakehouse(
        settings=settings,
        downloader=FakeDownloader(),
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )

    assert seen == ["ingest", "checkpoint"]
    assert (settings.current_dir / "storage" / "raw.parquet").is_file()
    assert result.promoted.previous_dir is not None
    assert not list((settings.lakehouse_dir / "builds").iterdir())
    assert not list((settings.lakehouse_dir / "retired").iterdir())


def test_postgresql_build_prune_failure_does_not_fail_a_promoted_build(
    tmp_path, monkeypatch
) -> None:
    settings = _postgresql_settings(tmp_path)
    _install_postgresql_stages(monkeypatch)

    def fail_prune(*_args, **_kwargs):
        raise LifecycleError("disk said no")

    monkeypatch.setattr(build_module, "prune_obsolete_builds", fail_prune)
    stream = StringIO()
    configure_logging("INFO", "json", stream=stream)

    result = build_lakehouse(
        settings=settings,
        downloader=FakeDownloader(),
        dbt_executable="dbt",
        python_executable="python",
        environment={},
        reserve_bytes=0,
    )

    assert (result.promoted.current_dir / "storage" / "raw.parquet").is_file()
    event_codes = {
        json.loads(line)["record"]["extra"].get("event_code")
        for line in stream.getvalue().splitlines()
    }
    assert "obsolete_builds_prune_failed" in event_codes


def test_space_check_does_not_require_room_for_data_already_on_disk(tmp_path, monkeypatch) -> None:
    """current/ and downloaded archives need no extra free space."""
    settings = _settings(tmp_path)
    settings.current_dir.mkdir(parents=True)
    (settings.current_dir / "data.parquet").write_bytes(b"x" * 100)
    monkeypatch.setattr(
        lifecycle.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=900, free=100),
    )

    build_module._check_space(
        settings,
        download_bytes=0,
        raw_bytes=10,
        reserve_bytes=0,
        temporary_size_factor=4,
        log=build_module.logger,
    )

    with pytest.raises(
        LifecycleError,
        match=r"downloads 10 \+ seeded raw 0 \+ new build 100 \+ spill 0 \+ reserve 0",
    ):
        build_module._check_space(
            settings,
            download_bytes=10,
            raw_bytes=10,
            reserve_bytes=0,
            temporary_size_factor=4,
            log=build_module.logger,
        )


def test_postgresql_build_failure_leaves_staged_build_for_retry(tmp_path, monkeypatch) -> None:
    """Unlike the old shared-storage design, a PostgreSQL-mode build now stages into its own
    builds/<id>/, untouched by anything else - so a post-ingestion failure can leave it in place
    for inspection/retry exactly like local mode already does, instead of needing crash-orphan
    cleanup or workspace deletion."""
    base = _settings(tmp_path)
    settings = Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    downloader = FakeDownloader()

    def ingest(
        artifacts, *, build_paths, pipelines_dir, progress, workers, chunk_size, catalog_target
    ):
        (catalog_target.storage_dir / "raw.parquet").write_text("raw", encoding="utf-8")
        return IngestionResult(
            "fixture", "raw", ("load-1",), catalog_target.url, catalog_target.storage_dir
        )

    monkeypatch.setattr(build_module, "ingest_snapshot", ingest)
    monkeypatch.setattr(
        build_module,
        "run_dbt",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(TransformationError("failed dbt")),
    )

    with pytest.raises(TransformationError):
        build_lakehouse(
            settings=settings,
            downloader=downloader,
            dbt_executable="dbt",
            python_executable="python",
            environment={},
            reserve_bytes=0,
        )

    staged = list((settings.lakehouse_dir / "builds").glob("*"))
    assert len(staged) == 1
    assert (staged[0] / "storage" / "raw.parquet").read_text(encoding="utf-8") == "raw"
    assert not settings.current_dir.exists()


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


def test_space_check_counts_seeded_raw_and_spill_for_postgresql_docker(
    tmp_path, monkeypatch
) -> None:
    base = _settings(tmp_path)
    settings = Settings(
        repository_root=base.repository_root,
        data_dir=base.data_dir,
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
        scratch_dir=tmp_path / "scratch",
    )
    current_raw = settings.current_dir / "storage" / "raw"
    current_raw.mkdir(parents=True)
    (current_raw / "a.parquet").write_bytes(b"x" * 30)
    staged_raw = settings.lakehouse_dir / "builds" / "staged" / "storage" / "raw"
    staged_raw.mkdir(parents=True)
    (staged_raw / "b.parquet").write_bytes(b"x" * 20)
    monkeypatch.setattr(
        lifecycle.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=1_000, free=0),
    )

    # seed 30 + 20, new build max(1 * 4, current 30) = 30, spill = current 30
    with pytest.raises(LifecycleError, match=r"seeded raw 50 \+ new build 30 \+ spill 30"):
        build_module._check_space(
            settings,
            download_bytes=0,
            raw_bytes=1,
            reserve_bytes=0,
            temporary_size_factor=4,
            log=build_module.logger,
        )
