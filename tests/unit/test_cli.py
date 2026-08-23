from dataclasses import dataclass
from io import StringIO
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from imdb_ducklake import cli
from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import (
    AcquisitionError,
    ConfigurationError,
    ImdbLakehouseError,
    IngestionError,
    LifecycleError,
    PromotionError,
    TransformationError,
    ValidationError,
)
from imdb_ducklake.lakehouse.lifecycle import BuildPaths, initialize_build

runner = CliRunner()


@dataclass
class FakeDownloader:
    client: object

    def download_all(self, *args, **kwargs):
        entry = SimpleNamespace(size_bytes=123)
        return (SimpleNamespace(manifest_entry=entry),) * 7


def test_download_command_reports_verified_archives(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "Downloader", FakeDownloader)

    result = runner.invoke(cli.app, ["download"])

    assert result.exit_code == 0
    assert "Verified 7 archives (861 bytes)." in result.stdout


def test_global_log_format_reaches_configuration_and_binds_run_id(tmp_path, monkeypatch) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        log_format="json",
    )
    received: dict[str, object] = {}
    run_ids: list[str] = []

    def load(**kwargs):
        received.update(kwargs)
        return settings

    monkeypatch.setattr(cli.Settings, "load", staticmethod(load))
    monkeypatch.setattr(cli, "Downloader", FakeDownloader)
    monkeypatch.setattr(
        cli,
        "start_run_context",
        lambda run_id: run_ids.append(run_id) or run_id,
    )

    result = runner.invoke(cli.app, ["--log-format", "json", "download"])

    assert result.exit_code == 0
    assert received["log_format"] == "json"
    assert len(run_ids) == 1
    assert run_ids[0]


def test_progress_collector_uses_rich_only_for_interactive_console(tmp_path) -> None:
    class InteractiveStream(StringIO):
        def isatty(self) -> bool:
            return True

    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    cli.configure_logging("INFO", "console", stream=InteractiveStream())

    interactive = cli._progress_collector(settings, "build-rich")

    assert isinstance(interactive, cli.RichProgressCollector)

    cli.configure_logging("INFO", "json", stream=StringIO())

    redirected = cli._progress_collector(settings, "build-structured")
    assert isinstance(redirected, cli.StructuredLogCollector)


def test_download_command_maps_domain_error_to_exit_code(monkeypatch) -> None:
    def fail(**_kwargs):
        raise AcquisitionError("source unavailable")

    monkeypatch.setattr(cli.Settings, "load", staticmethod(fail))

    result = runner.invoke(cli.app, ["download", "--force"])

    assert result.exit_code == cli.ExitCode.ACQUISITION_ERROR
    assert "Error: source unavailable" in result.output


def test_build_command_runs_complete_orchestrator_and_reports_promotion(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received = None

    def fake_build(**kwargs):
        nonlocal received
        received = kwargs
        return SimpleNamespace(
            build_id="complete-build",
            transformation=SimpleNamespace(stdout="dbt passed\n"),
            validation=SimpleNamespace(relation_count=31),
            promoted=SimpleNamespace(current_dir=settings.current_dir),
        )

    monkeypatch.setattr(cli, "build_lakehouse", fake_build)

    result = runner.invoke(cli.app, ["build", "--force-download"])

    assert result.exit_code == 0
    assert received is not None
    assert received["settings"] == settings
    assert received["force_download"] is True
    assert "Promoted build complete-build" in result.stdout
    assert "validating 31 relations" in result.stdout


def test_ingest_command_loads_isolated_build_and_reports_catalog(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    artifacts = (object(),) * 7
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "load_verified_artifacts", lambda *_args, **_kwargs: artifacts)

    def fake_ingest(
        received,
        *,
        build_paths,
        pipelines_dir,
        progress,
        workers,
        chunk_size,
    ):
        assert received == artifacts
        assert build_paths.storage_dir.is_dir()
        assert pipelines_dir == settings.dlt_pipelines_dir
        assert progress is not None
        assert workers == settings.ingest_workers
        assert chunk_size == settings.ingest_chunk_size
        build_paths.catalog_path.write_text("fixture", encoding="utf-8")
        return SimpleNamespace(load_ids=("load-1",), catalog_path=build_paths.catalog_path)

    monkeypatch.setattr(cli, "ingest_snapshot", fake_ingest)

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == 0
    assert "Loaded 7 archives into build" in result.stdout
    assert "(1 dlt load(s))." in result.stdout
    assert "Catalog:" in result.stdout
    assert settings.current_dir.exists() is False


def test_ingest_command_maps_archive_verification_error(monkeypatch) -> None:
    def fail(*_args, **_kwargs):
        raise AcquisitionError("retained archive is invalid")

    monkeypatch.setattr(cli, "load_verified_artifacts", fail)

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == cli.ExitCode.ACQUISITION_ERROR
    assert "Error: retained archive is invalid" in result.output


def test_ingest_preserves_existing_staged_build_without_explicit_replace(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    staged = BuildPaths.create(settings.lakehouse_dir, build_id="existing-build")
    initialize_build(staged)
    staged.catalog_path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "load_verified_artifacts", lambda *_args, **_kwargs: (object(),) * 7)

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == cli.ExitCode.LIFECYCLE_ERROR
    assert "pass --replace-staged" in result.output
    assert staged.temporary_dir.is_dir()


def test_ingest_command_uses_shared_catalog_when_configured(tmp_path, monkeypatch) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    artifacts = (object(),) * 7
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "load_verified_artifacts", lambda *_args, **_kwargs: artifacts)
    received = None

    def fake_ingest(received_artifacts, **kwargs):
        nonlocal received
        received = kwargs
        assert received_artifacts == artifacts
        return SimpleNamespace(load_ids=("load-1",), catalog_path="unused")

    monkeypatch.setattr(cli, "ingest_snapshot", fake_ingest)

    result = runner.invoke(cli.app, ["ingest"])

    assert result.exit_code == 0
    assert received is not None
    assert received["catalog_target"].storage_dir == received["build_paths"].storage_dir
    assert received["catalog_target"].storage_dir.is_dir()
    assert "postgresql://postgres:5432/ducklake_catalog#imdb_lake" in result.stdout
    assert "secret" not in result.stdout
    # Ingest only stages, exactly like local mode - a later transform/build promotes it.
    assert received["build_paths"].temporary_dir.exists()
    assert not settings.current_dir.exists()


def test_transform_command_runs_dbt_for_staged_build(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    paths = BuildPaths.create(settings.lakehouse_dir, build_id="fixture-build")
    initialize_build(paths)
    paths.catalog_path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "select_staged_build", lambda *_args, **_kwargs: paths)

    def fake_run(dbt_args, **kwargs):
        assert dbt_args == ("build",)
        assert kwargs["build_paths"] == paths
        assert kwargs["controller_path"] == settings.dbt_state_dir / "fixture-build.duckdb"
        return SimpleNamespace(stdout="dbt completed\n")

    monkeypatch.setattr(cli, "run_dbt", fake_run)

    result = runner.invoke(cli.app, ["transform", "--build-id", "fixture-build"])

    assert result.exit_code == 0
    assert "Transformed and tested build fixture-build; it remains unpromoted." in result.stdout


def test_transform_command_uses_shared_catalog_when_configured(tmp_path, monkeypatch) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    paths = BuildPaths.create(settings.lakehouse_dir, build_id="shared-run")
    initialize_build(paths)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "select_staged_build", lambda *_args, **_kwargs: paths)
    received = None

    def fake_run(dbt_args, **kwargs):
        nonlocal received
        received = kwargs
        assert dbt_args == ("build",)
        return SimpleNamespace(stdout="dbt completed\n")

    monkeypatch.setattr(cli, "run_dbt", fake_run)

    result = runner.invoke(cli.app, ["transform", "--build-id", "shared-run"])

    assert result.exit_code == 0
    assert received is not None
    assert received["build_paths"].build_id == "shared-run"
    # dbt attaches at this build's own isolated staging directory, never a shared/live path.
    assert received["catalog_target"].storage_dir == paths.storage_dir
    assert "PostgreSQL-backed DuckLake catalog" in result.stdout


def test_promote_command_validates_promotes_and_reattaches_current(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    paths = BuildPaths.create(settings.lakehouse_dir, build_id="fixture-build")
    initialize_build(paths)
    paths.catalog_path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    events: list[str] = []

    def fake_validate_build(received, **kwargs):
        assert received == paths
        assert kwargs["working_directory"] == settings.repository_root
        events.append("validate staged")
        return SimpleNamespace(relation_count=31)

    def fake_promote(received):
        assert received == paths
        events.append("promote")
        return SimpleNamespace(
            build_id=paths.build_id,
            current_dir=settings.current_dir,
            catalog_path=settings.current_dir / "catalog.duckdb",
            storage_dir=settings.current_dir / "storage",
        )

    def fake_checkpoint(catalog_path, storage_dir):
        assert catalog_path == settings.current_dir / "catalog.duckdb"
        assert storage_dir == settings.current_dir / "storage"
        events.append("checkpoint")

    def fake_validate_catalog(**kwargs):
        assert kwargs["catalog_path"] == settings.current_dir / "catalog.duckdb"
        assert kwargs["storage_dir"] == settings.current_dir / "storage"
        assert kwargs["build_id"] == paths.build_id
        events.append("validate current")
        return SimpleNamespace(
            relation_count=31,
            mart_row_counts={"mart_title_search": 2},
        )

    def fake_prune(received, *, keep_retired):
        assert received == settings.lakehouse_dir
        assert keep_retired == 1
        events.append("prune")
        return (settings.lakehouse_dir / "builds" / "stale-build",)

    monkeypatch.setattr(cli, "validate_build", fake_validate_build)
    monkeypatch.setattr(cli, "promote_build", fake_promote)
    monkeypatch.setattr(cli, "checkpoint_lakehouse", fake_checkpoint)
    monkeypatch.setattr(cli, "validate_catalog", fake_validate_catalog)
    monkeypatch.setattr(cli, "prune_obsolete_builds", fake_prune)

    result = runner.invoke(cli.app, ["promote", "--build-id", paths.build_id, "--prune"])

    assert result.exit_code == 0
    assert events == ["validate staged", "promote", "checkpoint", "validate current", "prune"]
    assert "Promoted build fixture-build" in result.stdout
    assert "reattaching 31 current relations read-only" in result.stdout
    assert "marts.mart_title_search: 2 rows" in result.stdout
    assert "Pruned 1 obsolete build workspace(s)" in result.stdout


def test_validate_command_automatically_selects_the_only_staged_build(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    paths = BuildPaths.create(settings.lakehouse_dir, build_id="staged-build")
    initialize_build(paths)
    paths.catalog_path.write_text("fixture", encoding="utf-8")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received = None

    def fake_validate(**kwargs):
        nonlocal received
        received = kwargs
        return SimpleNamespace(
            build_id="staged-build",
            relation_count=31,
            mart_row_counts={"mart_title_search": 2},
        )

    monkeypatch.setattr(cli, "validate_catalog", fake_validate)

    result = runner.invoke(cli.app, ["validate"])

    assert result.exit_code == 0
    assert received is not None
    assert received["catalog_path"] == paths.catalog_path
    assert received["storage_dir"] == paths.storage_dir
    assert "Validated staged-build: 31 required relations." in result.stdout
    assert "marts.mart_title_search: 2 rows" in result.stdout


def test_validate_command_prefers_current_without_requiring_arguments(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    settings.current_dir.mkdir(parents=True)
    catalog_path = settings.current_dir / "catalog.duckdb"
    catalog_path.write_text("fixture", encoding="utf-8")
    (settings.current_dir / "storage").mkdir()
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received = None

    def fake_validate(**kwargs):
        nonlocal received
        received = kwargs
        return SimpleNamespace(build_id="current", relation_count=31, mart_row_counts={})

    monkeypatch.setattr(cli, "validate_catalog", fake_validate)

    result = runner.invoke(cli.app, ["validate"])

    assert result.exit_code == 0
    assert received is not None
    assert received["catalog_path"] == catalog_path
    assert received["build_id"] == "current"
    assert "Validated current: 31 required relations." in result.stdout


def test_validate_command_uses_shared_catalog_when_configured(tmp_path, monkeypatch) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    (settings.lakehouse_dir / "storage").mkdir(parents=True)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received = None

    def fake_validate(paths, **kwargs):
        nonlocal received
        received = (paths, kwargs)
        return SimpleNamespace(build_id=paths.build_id, relation_count=30, mart_row_counts={})

    monkeypatch.setattr(cli, "validate_build", fake_validate)

    result = runner.invoke(cli.app, ["validate", "--build-id", "shared-run"])

    assert result.exit_code == 0
    assert received is not None
    assert received[0].build_id == "shared-run"
    assert received[1]["catalog_target"].safe_identity.endswith("#imdb_lake")
    assert "Validated shared-run: 30 required relations." in result.stdout


def test_checkpoint_command_uses_current_build_lock_and_structured_logs(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    settings.current_dir.mkdir(parents=True)
    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    catalog_path.write_text("fixture", encoding="utf-8")
    storage_dir.mkdir()
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received: list[tuple[object, object]] = []
    events: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def bind(self, **_kwargs):
            return self

        def info(self, message, **fields):
            events.append((message, fields))

        def error(self, message, **fields):
            events.append((message, fields))

    monkeypatch.setattr(cli, "logger", FakeLogger())

    def fake_checkpoint_lakehouse(catalog, storage):
        received.append((catalog, storage))
        return ()

    monkeypatch.setattr(cli, "checkpoint_lakehouse", fake_checkpoint_lakehouse)

    result = runner.invoke(cli.app, ["checkpoint"])

    assert result.exit_code == 0
    assert received == [(catalog_path, storage_dir)]
    assert [message for message, _fields in events] == [
        "Checkpoint started",
        "Checkpoint completed",
    ]
    assert events[0][1]["event_code"] == "checkpoint_started"
    assert events[1][1]["event_code"] == "checkpoint_completed"
    assert isinstance(events[1][1]["elapsed_seconds"], float)
    assert "Checkpointed current lakehouse in" in result.stdout


def test_checkpoint_command_logs_missing_current_as_lifecycle_failure(
    tmp_path, monkeypatch
) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    events: list[tuple[str, dict[str, object]]] = []

    class FakeLogger:
        def bind(self, **_kwargs):
            return self

        def info(self, message, **fields):
            events.append((message, fields))

        def error(self, message, **fields):
            events.append((message, fields))

    monkeypatch.setattr(cli, "logger", FakeLogger())

    result = runner.invoke(cli.app, ["checkpoint"])

    assert result.exit_code == cli.ExitCode.LIFECYCLE_ERROR
    assert "Current DuckLake catalog does not exist" in result.stderr
    assert [message for message, _fields in events] == ["Checkpoint failed"]
    assert events[0][1]["event_code"] == "checkpoint_failed"
    assert events[0][1]["error_type"] == "LifecycleError"


def test_checkpoint_command_uses_shared_catalog_when_configured(tmp_path, monkeypatch) -> None:
    settings = Settings(
        repository_root=tmp_path,
        data_dir=tmp_path / "data",
        catalog_url="postgresql://imdb:secret@postgres:5432/ducklake_catalog",
    )
    (settings.current_dir / "storage").mkdir(parents=True)
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    received = []

    def fake_checkpoint_catalog_target(target):
        received.append(target)
        return ()

    monkeypatch.setattr(cli, "checkpoint_catalog_target", fake_checkpoint_catalog_target)

    result = runner.invoke(cli.app, ["checkpoint"])

    assert result.exit_code == 0
    assert len(received) == 1
    assert received[0].safe_identity == "postgresql://postgres:5432/ducklake_catalog#imdb_lake"
    assert "Checkpointed shared PostgreSQL-backed lakehouse" in result.stdout


def test_main_invokes_typer_application(monkeypatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "app", fake_app)

    cli.main()

    assert called


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (ConfigurationError("configuration"), cli.ExitCode.CONFIGURATION_ERROR),
        (AcquisitionError("acquisition"), cli.ExitCode.ACQUISITION_ERROR),
        (IngestionError("ingestion"), cli.ExitCode.INGESTION_ERROR),
        (TransformationError("transformation"), cli.ExitCode.TRANSFORMATION_ERROR),
        (ValidationError("validation"), cli.ExitCode.VALIDATION_ERROR),
        (PromotionError("promotion"), cli.ExitCode.PROMOTION_ERROR),
        (LifecycleError("lifecycle"), cli.ExitCode.LIFECYCLE_ERROR),
        (ImdbLakehouseError("other"), cli.ExitCode.UNEXPECTED_APPLICATION_ERROR),
    ],
)
def test_expected_failures_have_stable_exit_codes(
    error: ImdbLakehouseError, expected: cli.ExitCode
) -> None:
    assert cli._exit_code_for(error) is expected
