from dataclasses import dataclass
from types import SimpleNamespace

from typer.testing import CliRunner

from imdb_ducklake import cli
from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import AcquisitionError

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

    assert result.exit_code == 1
    assert "Error: source unavailable" in result.output


def test_ingest_command_loads_isolated_build_and_reports_catalog(tmp_path, monkeypatch) -> None:
    settings = Settings(repository_root=tmp_path, data_dir=tmp_path / "data")
    artifacts = (object(),) * 7
    monkeypatch.setattr(cli.Settings, "load", staticmethod(lambda **_kwargs: settings))
    monkeypatch.setattr(cli, "load_verified_artifacts", lambda *_args, **_kwargs: artifacts)

    def fake_ingest(received, *, build_paths, pipelines_dir):
        assert received == artifacts
        assert build_paths.storage_dir.is_dir()
        assert pipelines_dir == settings.dlt_pipelines_dir
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

    assert result.exit_code == 1
    assert "Error: retained archive is invalid" in result.output


def test_main_invokes_typer_application(monkeypatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "app", fake_app)

    cli.main()

    assert called
