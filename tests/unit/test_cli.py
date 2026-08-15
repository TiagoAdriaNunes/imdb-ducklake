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


def test_main_invokes_typer_application(monkeypatch) -> None:
    called = False

    def fake_app() -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(cli, "app", fake_app)

    cli.main()

    assert called
