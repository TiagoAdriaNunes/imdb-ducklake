"""End-to-end application use case for building and promoting a lakehouse."""

from __future__ import annotations

import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from dlt.common.runtime.collector_base import Collector
from loguru import logger

if TYPE_CHECKING:
    from loguru import Logger

from imdb_ducklake.acquisition.downloader import Downloader, VerifiedArtifact
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import LifecycleError
from imdb_ducklake.ingestion.pipeline import IngestionResult, ingest_snapshot
from imdb_ducklake.ingestion.progress import StructuredLogCollector
from imdb_ducklake.lakehouse.catalog import CatalogTarget
from imdb_ducklake.lakehouse.lifecycle import (
    BuildLock,
    BuildPaths,
    PromotedBuild,
    SpaceBudget,
    checkpoint_lakehouse,
    cleanup_build,
    ensure_free_space,
    initialize_build,
    promote_build,
    prune_obsolete_builds,
    recover_interrupted_promotion,
)
from imdb_ducklake.lakehouse.validation import ValidationResult, validate_build
from imdb_ducklake.transformation.dbt_runner import DbtRunResult, run_dbt

_DEFAULT_RESERVE_BYTES = 1024**3
_DEFAULT_TEMPORARY_SIZE_FACTOR = 4


@dataclass(frozen=True, slots=True)
class BuildResult:
    """Stable summary of a fully validated and promoted lakehouse build."""

    artifacts: tuple[VerifiedArtifact, ...]
    ingestion: IngestionResult
    transformation: DbtRunResult
    validation: ValidationResult
    promoted: PromotedBuild

    @property
    def build_id(self) -> str:
        return self.promoted.build_id


def build_lakehouse(
    *,
    settings: Settings,
    downloader: Downloader,
    dbt_executable: str,
    python_executable: str,
    environment: Mapping[str, str],
    force_download: bool = False,
    show_progress: bool = True,
    progress_factory: Callable[[str], Collector] | None = None,
    reserve_bytes: int = _DEFAULT_RESERVE_BYTES,
    temporary_size_factor: int = _DEFAULT_TEMPORARY_SIZE_FACTOR,
) -> BuildResult:
    """Acquire, ingest, transform, validate, and publish one snapshot."""
    if reserve_bytes < 0:
        raise ValueError("reserve_bytes must be non-negative")
    if temporary_size_factor < 1:
        raise ValueError("temporary_size_factor must be at least one")

    started = time.monotonic()
    paths = BuildPaths.create(settings.lakehouse_dir)
    catalog_target = (
        CatalogTarget(settings.catalog_url, settings.lakehouse_dir / "storage")
        if settings.catalog_url is not None
        else None
    )
    build_logger = logger.bind(build_id=paths.build_id)
    lock_path = settings.lakehouse_dir / ".build.lock"
    build_logger.info(
        "Waiting for build lock",
        event_code="build_lock_waiting",
        stage="lifecycle",
        status="waiting",
        path=str(lock_path),
    )
    with BuildLock(lock_path):
        build_logger.info(
            "Build lock acquired",
            event_code="build_lock_acquired",
            stage="lifecycle",
            status="completed",
        )
        if catalog_target is None:
            recover_interrupted_promotion(settings.lakehouse_dir)
        _check_space(
            settings,
            raw_bytes=_directory_size(settings.raw_dir),
            reserve_bytes=reserve_bytes,
            temporary_size_factor=temporary_size_factor,
            log=build_logger,
        )

        build_logger.info(
            "Acquisition started",
            event_code="acquisition_started",
            stage="acquisition",
            status="started",
            force=force_download,
        )
        artifacts = downloader.download_all(
            DATASETS,
            raw_dir=settings.raw_dir,
            manifest_path=settings.manifest_path,
            force=force_download,
        )
        raw_bytes = sum(artifact.manifest_entry.size_bytes for artifact in artifacts)
        build_logger.info(
            "Acquisition completed",
            event_code="acquisition_completed",
            stage="acquisition",
            status="completed",
            files=len(artifacts),
            bytes=raw_bytes,
        )

        _check_space(
            settings,
            raw_bytes=raw_bytes,
            reserve_bytes=reserve_bytes,
            temporary_size_factor=temporary_size_factor,
            log=build_logger,
        )
        removed = (
            prune_obsolete_builds(settings.lakehouse_dir, keep_retired=1)
            if catalog_target is None
            else ()
        )
        if removed:
            build_logger.info(
                "Obsolete builds pruned",
                event_code="obsolete_builds_pruned",
                stage="lifecycle",
                status="completed",
                count=len(removed),
            )

        initialize_build(paths)
        build_logger.info(
            "Ingestion started",
            event_code="ingestion_started",
            stage="ingestion",
            status="started",
        )
        try:
            ingestion = ingest_snapshot(
                artifacts,
                build_paths=paths,
                pipelines_dir=settings.dlt_pipelines_dir,
                progress=(
                    progress_factory(paths.build_id)
                    if show_progress and progress_factory is not None
                    else StructuredLogCollector(
                        build_id=paths.build_id,
                        log_period=settings.progress_interval_seconds,
                    )
                    if show_progress
                    else None
                ),
                catalog_target=catalog_target,
            )
        except BaseException:
            # An incomplete or failed raw load cannot be safely resumed, so this is the one
            # stage that still discards the build workspace on failure.
            cleanup_build(paths)
            raise

        # From here on, ingestion has already produced a valid raw build. A later failure
        # (dbt/validate/promote) must not delete it: retrying acquisition and ingestion just to
        # re-test a dbt fix can cost several minutes for no reason when the raw archives never
        # changed. Leave the build staged under data/ducklake/builds/ so `make transform` or
        # `make promote --build-id ...` can resume it directly.
        try:
            build_logger.info(
                "dbt build started",
                event_code="dbt_build_started",
                stage="dbt",
                status="started",
            )
            transformation = run_dbt(
                ("build",),
                build_paths=paths,
                project_dir=settings.dbt_project_dir,
                profiles_dir=settings.dbt_project_dir,
                controller_path=settings.dbt_state_dir / f"{paths.build_id}.duckdb",
                executable=dbt_executable,
                environment=environment,
                catalog_target=catalog_target,
            )
            build_logger.info(
                "dbt build completed",
                event_code="dbt_build_completed",
                stage="dbt",
                status="completed",
            )

            build_logger.info(
                "Validation started",
                event_code="validation_started",
                stage="validation",
                status="started",
            )
            validation = validate_build(
                paths,
                executable=python_executable,
                environment=environment,
                working_directory=settings.repository_root,
                catalog_target=catalog_target,
            )
            build_logger.info(
                "Validation completed",
                event_code="validation_completed",
                stage="validation",
                status="completed",
                relation_count=validation.relation_count,
                mart_count=len(validation.mart_row_counts),
                mart_row_counts=validation.mart_row_counts,
            )

            if catalog_target is None:
                build_logger.info(
                    "Promotion started",
                    event_code="promotion_started",
                    stage="promotion",
                    status="started",
                )
                promoted = promote_build(paths)
                build_logger.info(
                    "Promotion completed",
                    event_code="promotion_completed",
                    stage="promotion",
                    status="completed",
                    current=str(promoted.current_dir),
                )

                checkpoint_lakehouse(promoted.catalog_path, promoted.storage_dir)
                build_logger.info(
                    "Checkpoint completed",
                    event_code="checkpoint_completed",
                    stage="checkpoint",
                    status="completed",
                )
            else:
                promoted = PromotedBuild(
                    build_id=paths.build_id,
                    current_dir=catalog_target.storage_dir.parent,
                    # Compatibility-only identity for BuildResult consumers. No catalog file is
                    # created; PostgreSQL is the authoritative metadata catalog.
                    catalog_path=catalog_target.storage_dir.parent / ".postgresql-catalog",
                    storage_dir=catalog_target.storage_dir,
                    previous_dir=None,
                )
                cleanup_build(paths)
                build_logger.info(
                    "PostgreSQL-backed DuckLake build published",
                    event_code="shared_catalog_build_published",
                    stage="publication",
                    status="completed",
                    storage=str(catalog_target.storage_dir),
                )
        except BaseException:
            build_logger.error(
                "Build stage failed after ingestion; raw build preserved for retry",
                event_code="post_ingestion_stage_failed",
                stage="build",
                status="failed",
                build_id=paths.build_id,
                catalog=(
                    catalog_target.safe_identity
                    if catalog_target is not None
                    else str(paths.catalog_path)
                ),
            )
            raise

    build_logger.info(
        "Lakehouse build completed",
        event_code="lakehouse_build_completed",
        stage="build",
        status="completed",
        elapsed_seconds=round(time.monotonic() - started, 2),
        current=str(promoted.current_dir),
    )
    return BuildResult(artifacts, ingestion, transformation, validation, promoted)


def _check_space(
    settings: Settings,
    *,
    raw_bytes: int,
    reserve_bytes: int,
    temporary_size_factor: int,
    log: Logger,
) -> None:
    current_bytes = _directory_size(settings.current_dir)
    estimated_temporary_bytes = max(raw_bytes * temporary_size_factor, current_bytes)
    budget = SpaceBudget(
        raw_archives_bytes=raw_bytes,
        current_build_bytes=current_bytes,
        temporary_build_bytes=estimated_temporary_bytes,
        reserve_bytes=reserve_bytes,
    )
    free_bytes = ensure_free_space(settings.data_dir, budget)
    log.info(
        "Free-space check passed",
        event_code="free_space_gate_passed",
        stage="lifecycle",
        status="completed",
        required_bytes=budget.required_bytes,
        available_bytes=free_bytes,
    )


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError as error:
        raise LifecycleError(f"Could not measure disk usage for {path}") from error
