"""Command-line composition root for the IMDb DuckLake application."""

from os import environ
from os import name as os_name
from pathlib import Path
from sys import executable as python_executable
from typing import Annotated

import httpx
import typer

from imdb_ducklake.acquisition.downloader import Downloader, load_verified_artifacts
from imdb_ducklake.application.build import build_lakehouse
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import ImdbLakehouseError, LifecycleError
from imdb_ducklake.ingestion.pipeline import ingest_snapshot
from imdb_ducklake.lakehouse.lifecycle import (
    BuildLock,
    BuildPaths,
    list_staged_builds,
    prune_obsolete_builds,
    recover_interrupted_promotion,
    select_staged_build,
    temporary_build,
)
from imdb_ducklake.lakehouse.validation import validate_catalog
from imdb_ducklake.observability import configure_logging
from imdb_ducklake.transformation.dbt_runner import run_dbt

app = typer.Typer(
    name="imdb-lakehouse",
    help="Build and maintain a local IMDb analytics lakehouse.",
    no_args_is_help=True,
)


@app.callback()
def root_command() -> None:
    """Select a lakehouse operation."""


@app.command("build")
def build_command(
    force_download: Annotated[
        bool,
        typer.Option(
            "--force-download",
            help="Download every archive before building, even when verified copies exist.",
        ),
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
    """Build, validate, and atomically promote a complete IMDb lakehouse."""
    try:
        settings = Settings.load(data_dir=data_dir)
        configure_logging(settings.log_level)
        timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "imdb-ducklake/0.1"},
        ) as client:
            result = build_lakehouse(
                settings=settings,
                downloader=Downloader(client),
                dbt_executable=str(_dbt_executable()),
                python_executable=python_executable,
                environment=environ,
                force_download=force_download,
            )
        typer.echo(result.transformation.stdout.rstrip())
        typer.echo(
            f"Promoted build {result.build_id} to {result.promoted.current_dir} "
            f"after validating {result.validation.relation_count} relations."
        )
    except ImdbLakehouseError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


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


@app.command("ingest")
def ingest_command(
    replace_staged: Annotated[
        bool,
        typer.Option(
            "--replace-staged",
            help="Delete existing unpromoted builds before loading a fresh snapshot.",
        ),
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
    """Load retained verified archives into an isolated raw DuckLake build."""
    try:
        settings = Settings.load(data_dir=data_dir)
        configure_logging(settings.log_level)
        artifacts = load_verified_artifacts(
            DATASETS,
            raw_dir=settings.raw_dir,
            manifest_path=settings.manifest_path,
        )
        paths = BuildPaths.create(settings.lakehouse_dir)
        with BuildLock(paths.lock_path):
            recover_interrupted_promotion(settings.lakehouse_dir)
            staged = list_staged_builds(settings.lakehouse_dir)
            if staged and not replace_staged:
                staged_ids = ", ".join(build.build_id for build in staged)
                raise LifecycleError(
                    "A staged build already exists. Run transform first or pass "
                    f"--replace-staged to discard it: {staged_ids}"
                )
            if replace_staged:
                prune_obsolete_builds(settings.lakehouse_dir)
            with temporary_build(paths):
                result = ingest_snapshot(
                    artifacts,
                    build_paths=paths,
                    pipelines_dir=settings.dlt_pipelines_dir,
                    show_progress=True,
                )
        typer.echo(
            f"Loaded {len(artifacts)} archives into build {paths.build_id} "
            f"({len(result.load_ids)} dlt load(s))."
        )
        typer.echo(f"Catalog: {result.catalog_path}")
    except ImdbLakehouseError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command("transform")
def transform_command(
    build_id: Annotated[
        str | None,
        typer.Option("--build-id", help="Transform a specific staged build ID."),
    ] = None,
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
    """Run all dbt models and tests against one staged DuckLake build."""
    try:
        settings = Settings.load(data_dir=data_dir)
        configure_logging(settings.log_level)
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            recover_interrupted_promotion(settings.lakehouse_dir)
            paths = select_staged_build(settings.lakehouse_dir, build_id=build_id)
            result = run_dbt(
                ("build",),
                build_paths=paths,
                project_dir=settings.dbt_project_dir,
                profiles_dir=settings.dbt_project_dir,
                controller_path=settings.dbt_state_dir / f"{paths.build_id}.duckdb",
                executable=str(_dbt_executable()),
                environment=environ,
            )
        typer.echo(result.stdout.rstrip())
        typer.echo(f"Transformed and tested build {paths.build_id}; it remains unpromoted.")
    except ImdbLakehouseError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


@app.command("validate")
def validate_command(
    build_id: Annotated[
        str | None,
        typer.Option(
            "--build-id",
            help="Validate a specific staged build instead of the active current build.",
        ),
    ] = None,
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
    """Validate the current build, or the sole staged build when no current exists."""
    try:
        settings = Settings.load(data_dir=data_dir)
        configure_logging(settings.log_level)
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            recover_interrupted_promotion(settings.lakehouse_dir)
            if build_id is not None:
                paths = select_staged_build(settings.lakehouse_dir, build_id=build_id)
                catalog_path = paths.catalog_path
                storage_dir = paths.storage_dir
                selected_id = paths.build_id
            elif settings.current_dir.exists():
                catalog_path = settings.current_dir / "catalog.duckdb"
                storage_dir = settings.current_dir / "storage"
                selected_id = "current"
            else:
                paths = select_staged_build(settings.lakehouse_dir)
                catalog_path = paths.catalog_path
                storage_dir = paths.storage_dir
                selected_id = paths.build_id
            result = validate_catalog(
                catalog_path=catalog_path,
                storage_dir=storage_dir,
                build_id=selected_id,
                executable=python_executable,
                environment=environ,
                working_directory=settings.repository_root,
            )
        typer.echo(f"Validated {result.build_id}: {result.relation_count} required relations.")
        for relation, row_count in sorted(result.mart_row_counts.items()):
            typer.echo(f"  marts.{relation}: {row_count:,} rows")
    except ImdbLakehouseError as error:
        typer.echo(f"Error: {error}", err=True)
        raise typer.Exit(code=1) from error


def main() -> None:
    """Run the Typer application."""
    app()


def _dbt_executable() -> Path:
    return Path(python_executable).with_name("dbt.exe" if os_name == "nt" else "dbt")
