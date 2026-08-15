from dataclasses import dataclass
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
    assert "dbt passed" in result.stdout
    assert "Promoted build complete-build" in result.stdout
    assert "validating 31 relations" in result.stdout


def test_ingest_command_loads_isolated_build_and_reports_catalog(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    artifacts = (object(),) * 7
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "load_verified_artifacts", lambda *_args, **_kwargs: artifacts)

    def fake_ingest(received, *, build_paths, pipelines_dir, show_progress):
        assert received == artifacts
        assert build_paths.storage_dir.is_dir()
        assert pipelines_dir == settings.dlt_pipelines_dir
        assert show_progress is True
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
    assert "dbt completed" in result.stdout
    assert "Transformed and tested build fixture-build; it remains unpromoted." in result.stdout


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
