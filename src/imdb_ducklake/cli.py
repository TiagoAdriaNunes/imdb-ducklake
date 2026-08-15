"""Command-line composition root for the IMDb DuckLake application."""

from pathlib import Path
from typing import Annotated

import httpx
import typer

from imdb_ducklake.acquisition.downloader import Downloader
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import ImdbLakehouseError
from imdb_ducklake.observability import configure_logging

app = typer.Typer(
    name="imdb-lakehouse",
    help="Build and maintain a local IMDb analytics lakehouse.",
    no_args_is_help=True,
)


@app.callback()
def root_command() -> None:
    """Select a lakehouse operation."""


@app.command("download")
def download_command(
    force: Annotated[
        bool,
        typer.Option("--force", help="Download every archive even when a verified copy exists."),
    ] = False,
    data_dir: Annotated[
        Path | None,
        typer.Option(
            "--data-dir",
            help="Override the repository-relative data directory.",
            file_okay=False,
            dir_okay=True,
        ),
    ] = None,
) -> None:
    """Download and verify all seven IMDb source archives."""
    try:
        settings = Settings.load(data_dir=data_dir)
        configure_logging(settings.log_level)
        timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "imdb-ducklake/0.1"},
        ) as client:
            artifacts = Downloader(client).download_all(
                DATASETS,
                raw_dir=settings.raw_dir,
                manifest_path=settings.manifest_path,
                force=force,
            )
        total_bytes = sum(artifact.manifest_entry.size_bytes for artifact in artifacts)
        typer.echo(f"Verified {len(artifacts)} archives ({total_bytes:,} bytes).")
    except ImdbLakehouseError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


def main() -> None:
    """Run the Typer application."""
    app()
