"""Command-line composition root for the IMDb DuckLake application."""

from dataclasses import dataclass
from enum import IntEnum
from os import environ
from os import name as os_name
from pathlib import Path
from sys import executable as python_executable
from time import monotonic
from typing import Annotated, NoReturn
from uuid import uuid4

import httpx
import typer
from dlt.common.runtime.collector_base import Collector
from loguru import logger

from imdb_ducklake import __version__
from imdb_ducklake.acquisition.downloader import Downloader, load_verified_artifacts
from imdb_ducklake.application.build import build_lakehouse
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
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
from imdb_ducklake.ingestion.pipeline import ingest_snapshot
from imdb_ducklake.ingestion.progress import RichProgressCollector, StructuredLogCollector
from imdb_ducklake.lakehouse.catalog import CatalogTarget
from imdb_ducklake.lakehouse.lifecycle import (
    BuildLock,
    BuildPaths,
    checkpoint_catalog_target,
    checkpoint_lakehouse,
    cleanup_build,
    list_staged_builds,
    promote_build,
    prune_obsolete_builds,
    recover_interrupted_promotion,
    select_staged_build,
    temporary_build,
)
from imdb_ducklake.lakehouse.validation import validate_build, validate_catalog
from imdb_ducklake.observability import (
    configure_logging,
    get_console,
    rich_progress_enabled,
    start_run_context,
)
from imdb_ducklake.transformation.dbt_runner import run_dbt

app = typer.Typer(
    name="imdb-lakehouse",
    help="Build and maintain a local IMDb analytics lakehouse.",
    no_args_is_help=True,
)


class ExitCode(IntEnum):
    """Stable process exit codes for expected application failures."""

    UNEXPECTED_APPLICATION_ERROR = 1
    CONFIGURATION_ERROR = 10
    ACQUISITION_ERROR = 11
    INGESTION_ERROR = 12
    TRANSFORMATION_ERROR = 13
    VALIDATION_ERROR = 14
    PROMOTION_ERROR = 15
    LIFECYCLE_ERROR = 16


@dataclass(frozen=True, slots=True)
class CliRuntime:
    """Options and correlation context shared by one CLI invocation."""

    log_format: str | None
    run_id: str


@app.callback()
def root_command(
    ctx: typer.Context,
    log_format: Annotated[
        str | None,
        typer.Option(
            "--log-format",
            help="Render diagnostic logs as human-readable console text or JSON Lines.",
            envvar="IMDB_LAKEHOUSE_LOG_FORMAT",
            metavar="console|json",
        ),
    ] = None,
) -> None:
    """Select a lakehouse operation."""
    ctx.obj = CliRuntime(log_format=log_format, run_id=str(uuid4()))


@app.command("build")
def build_command(
    ctx: typer.Context,
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
    """Build and validate a complete IMDb lakehouse."""
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": f"imdb-ducklake/{__version__}"},
        ) as client:
            result = build_lakehouse(
                settings=settings,
                downloader=Downloader(client),
                dbt_executable=str(_dbt_executable()),
                python_executable=python_executable,
                environment=environ,
                force_download=force_download,
                progress_factory=lambda build_id: _progress_collector(settings, build_id),
            )
        action = "Published" if settings.catalog_url is not None else "Promoted"
        typer.echo(
            f"{action} build {result.build_id} to {result.promoted.current_dir} "
            f"after validating {result.validation.relation_count} relations."
        )
    except ImdbLakehouseError as error:
        _exit_with_error(error)


@app.command("download")
def download_command(
    ctx: typer.Context,
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
        settings = _settings_for_command(ctx, data_dir=data_dir)
        timeout = httpx.Timeout(connect=30, read=120, write=30, pool=30)
        with httpx.Client(
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": f"imdb-ducklake/{__version__}"},
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
        _exit_with_error(error)


@app.command("ingest")
def ingest_command(
    ctx: typer.Context,
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
    """Load verified archives into a staged or configured shared DuckLake catalog."""
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        artifacts = load_verified_artifacts(
            DATASETS,
            raw_dir=settings.raw_dir,
            manifest_path=settings.manifest_path,
        )
        paths = BuildPaths.create(settings.lakehouse_dir)
        catalog_target = _catalog_target(settings)
        with BuildLock(paths.lock_path):
            if catalog_target is None:
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
                if catalog_target is None:
                    result = ingest_snapshot(
                        artifacts,
                        build_paths=paths,
                        pipelines_dir=settings.dlt_pipelines_dir,
                        progress=_progress_collector(settings, paths.build_id),
                    )
                else:
                    result = ingest_snapshot(
                        artifacts,
                        build_paths=paths,
                        pipelines_dir=settings.dlt_pipelines_dir,
                        progress=_progress_collector(settings, paths.build_id),
                        catalog_target=catalog_target,
                    )
            if catalog_target is not None:
                cleanup_build(paths)
        typer.echo(
            f"Loaded {len(artifacts)} archives into build {paths.build_id} "
            f"({len(result.load_ids)} dlt load(s))."
        )
        typer.echo(
            f"Catalog: {catalog_target.safe_identity}"
            if catalog_target is not None
            else f"Catalog: {result.catalog_path}"
        )
    except ImdbLakehouseError as error:
        _exit_with_error(error)


@app.command("transform")
def transform_command(
    ctx: typer.Context,
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
    """Run dbt against a staged build or the configured shared DuckLake catalog."""
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        catalog_target = _catalog_target(settings)
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            if catalog_target is None:
                recover_interrupted_promotion(settings.lakehouse_dir)
                paths = select_staged_build(settings.lakehouse_dir, build_id=build_id)
            else:
                paths = BuildPaths.create(settings.lakehouse_dir, build_id=build_id)
            run_dbt(
                ("build",),
                build_paths=paths,
                project_dir=settings.dbt_project_dir,
                profiles_dir=settings.dbt_project_dir,
                controller_path=settings.dbt_state_dir / f"{paths.build_id}.duckdb",
                executable=str(_dbt_executable()),
                environment=environ,
                catalog_target=catalog_target,
            )
        if catalog_target is None:
            typer.echo(f"Transformed and tested build {paths.build_id}; it remains unpromoted.")
        else:
            typer.echo(
                "Transformed and tested the PostgreSQL-backed DuckLake catalog "
                f"{catalog_target.safe_identity}."
            )
    except ImdbLakehouseError as error:
        _exit_with_error(error)


@app.command("promote")
def promote_command(
    ctx: typer.Context,
    build_id: Annotated[
        str | None,
        typer.Option("--build-id", help="Promote a specific staged build ID."),
    ] = None,
    prune: Annotated[
        bool,
        typer.Option(
            "--prune",
            help=(
                "After successful promotion and validation, remove other staged builds and "
                "retain only the newest rollback build."
            ),
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
    """Validate and atomically promote one staged DuckLake build."""
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            recover_interrupted_promotion(settings.lakehouse_dir)
            paths = select_staged_build(settings.lakehouse_dir, build_id=build_id)
            staged_validation = validate_build(
                paths,
                executable=python_executable,
                environment=environ,
                working_directory=settings.repository_root,
            )
            promoted = promote_build(paths)
            checkpoint_lakehouse(promoted.catalog_path, promoted.storage_dir)
            current_validation = validate_catalog(
                catalog_path=promoted.catalog_path,
                storage_dir=promoted.storage_dir,
                build_id=promoted.build_id,
                executable=python_executable,
                environment=environ,
                working_directory=settings.repository_root,
            )
            removed = prune_obsolete_builds(settings.lakehouse_dir, keep_retired=1) if prune else ()
        typer.echo(
            f"Promoted build {promoted.build_id} to {promoted.current_dir} after validating "
            f"{staged_validation.relation_count} staged relations and reattaching "
            f"{current_validation.relation_count} current relations read-only."
        )
        for relation, row_count in sorted(current_validation.mart_row_counts.items()):
            typer.echo(f"  marts.{relation}: {row_count:,} rows")
        if prune:
            typer.echo(
                f"Pruned {len(removed)} obsolete build workspace(s); retained one rollback build."
            )
    except ImdbLakehouseError as error:
        _exit_with_error(error)


@app.command("validate")
def validate_command(
    ctx: typer.Context,
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
    """Validate the configured shared catalog, current build, or sole staged build."""
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        catalog_target = _catalog_target(settings)
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            if catalog_target is not None:
                paths = BuildPaths.create(settings.lakehouse_dir, build_id=build_id)
                result = validate_build(
                    paths,
                    executable=python_executable,
                    environment=environ,
                    working_directory=settings.repository_root,
                    catalog_target=catalog_target,
                )
            elif build_id is not None:
                recover_interrupted_promotion(settings.lakehouse_dir)
                paths = select_staged_build(settings.lakehouse_dir, build_id=build_id)
                catalog_path = paths.catalog_path
                storage_dir = paths.storage_dir
                selected_id = paths.build_id
            elif settings.current_dir.exists():
                recover_interrupted_promotion(settings.lakehouse_dir)
                catalog_path = settings.current_dir / "catalog.duckdb"
                storage_dir = settings.current_dir / "storage"
                selected_id = "current"
            else:
                recover_interrupted_promotion(settings.lakehouse_dir)
                paths = select_staged_build(settings.lakehouse_dir)
                catalog_path = paths.catalog_path
                storage_dir = paths.storage_dir
                selected_id = paths.build_id
            if catalog_target is None:
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
        _exit_with_error(error)


@app.command("checkpoint")
def checkpoint_command(
    ctx: typer.Context,
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
    """Checkpoint the configured shared catalog or active current DuckLake build."""
    started = monotonic()
    checkpoint_logger = logger
    try:
        settings = _settings_for_command(ctx, data_dir=data_dir)
        catalog_target = _catalog_target(settings)
        catalog_path = settings.current_dir / "catalog.duckdb"
        storage_dir = (
            catalog_target.storage_dir
            if catalog_target is not None
            else settings.current_dir / "storage"
        )
        checkpoint_logger = logger.bind(
            target="shared" if catalog_target is not None else "current",
            catalog=(
                catalog_target.safe_identity if catalog_target is not None else str(catalog_path)
            ),
        )
        with BuildLock(settings.lakehouse_dir / ".build.lock"):
            if catalog_target is None:
                recover_interrupted_promotion(settings.lakehouse_dir)
            if catalog_target is None and not catalog_path.is_file():
                raise LifecycleError(f"Current DuckLake catalog does not exist: {catalog_path}")
            if not storage_dir.is_dir():
                raise LifecycleError(f"Current DuckLake storage does not exist: {storage_dir}")
            checkpoint_logger.info(
                "Checkpoint started",
                event_code="checkpoint_started",
                stage="checkpoint",
                status="started",
            )
            if catalog_target is None:
                checkpoint_lakehouse(catalog_path, storage_dir)
            else:
                checkpoint_catalog_target(catalog_target)
        elapsed_seconds = round(monotonic() - started, 2)
        checkpoint_logger.info(
            "Checkpoint completed",
            event_code="checkpoint_completed",
            stage="checkpoint",
            status="completed",
            elapsed_seconds=elapsed_seconds,
        )
        target_label = (
            "shared PostgreSQL-backed lakehouse"
            if catalog_target is not None
            else "current lakehouse"
        )
        typer.echo(f"Checkpointed {target_label} in {elapsed_seconds:.2f} seconds.")
    except ImdbLakehouseError as error:
        checkpoint_logger.error(
            "Checkpoint failed",
            event_code="checkpoint_failed",
            stage="checkpoint",
            status="failed",
            elapsed_seconds=round(monotonic() - started, 2),
            error_type=type(error).__name__,
            error_message=str(error),
        )
        _exit_with_error(error)


def main() -> None:
    """Run the Typer application."""
    app()


def _dbt_executable() -> Path:
    return Path(python_executable).with_name("dbt.exe" if os_name == "nt" else "dbt")


def _settings_for_command(ctx: typer.Context, *, data_dir: Path | None) -> Settings:
    runtime = ctx.obj
    if not isinstance(runtime, CliRuntime):
        runtime = CliRuntime(log_format=None, run_id=str(uuid4()))
    settings = Settings.load(data_dir=data_dir, log_format=runtime.log_format)
    configure_logging(settings.log_level, settings.log_format)
    start_run_context(runtime.run_id)
    return settings


def _progress_collector(settings: Settings, build_id: str) -> Collector:
    if rich_progress_enabled():
        return RichProgressCollector(console=get_console())
    return StructuredLogCollector(
        build_id=build_id,
        log_period=settings.progress_interval_seconds,
    )


def _catalog_target(settings: Settings) -> CatalogTarget | None:
    if settings.catalog_url is None:
        return None
    return CatalogTarget(settings.catalog_url, settings.lakehouse_dir / "storage")


def _exit_code_for(error: ImdbLakehouseError) -> ExitCode:
    """Map an expected domain failure to its stable process exit code."""
    if isinstance(error, ConfigurationError):
        return ExitCode.CONFIGURATION_ERROR
    if isinstance(error, AcquisitionError):
        return ExitCode.ACQUISITION_ERROR
    if isinstance(error, IngestionError):
        return ExitCode.INGESTION_ERROR
    if isinstance(error, TransformationError):
        return ExitCode.TRANSFORMATION_ERROR
    if isinstance(error, ValidationError):
        return ExitCode.VALIDATION_ERROR
    if isinstance(error, PromotionError):
        return ExitCode.PROMOTION_ERROR
    if isinstance(error, LifecycleError):
        return ExitCode.LIFECYCLE_ERROR
    return ExitCode.UNEXPECTED_APPLICATION_ERROR


def _exit_with_error(error: ImdbLakehouseError) -> NoReturn:
    """Render an expected failure and terminate with its category code."""
    typer.echo(f"Error: {error}", err=True)
    raise typer.Exit(code=int(_exit_code_for(error))) from error
