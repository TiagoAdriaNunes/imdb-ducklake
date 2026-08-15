"""End-to-end application use case for building and promoting a lakehouse."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from imdb_ducklake.acquisition.downloader import Downloader, VerifiedArtifact
from imdb_ducklake.config import Settings
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import LifecycleError
from imdb_ducklake.ingestion.pipeline import IngestionResult, ingest_snapshot
from imdb_ducklake.lakehouse.lifecycle import (
    BuildLock,
    BuildPaths,
    PromotedBuild,
    SpaceBudget,
    ensure_free_space,
    promote_build,
    prune_obsolete_builds,
    recover_interrupted_promotion,
    temporary_build,
)
from imdb_ducklake.lakehouse.validation import ValidationResult, validate_build
from imdb_ducklake.transformation.dbt_runner import DbtRunResult, run_dbt

logger = logging.getLogger(__name__)

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
    reserve_bytes: int = _DEFAULT_RESERVE_BYTES,
    temporary_size_factor: int = _DEFAULT_TEMPORARY_SIZE_FACTOR,
) -> BuildResult:
    """Acquire, ingest, transform, validate, and atomically promote one snapshot."""
    if reserve_bytes < 0:
        raise ValueError("reserve_bytes must be non-negative")
    if temporary_size_factor < 1:
        raise ValueError("temporary_size_factor must be at least one")

    started = time.monotonic()
    lock_path = settings.lakehouse_dir / ".build.lock"
    logger.info("Waiting for lakehouse build lock: path=%s", lock_path)
    with BuildLock(lock_path):
        logger.info("Acquired lakehouse build lock")
        recover_interrupted_promotion(settings.lakehouse_dir)
        _check_space(
            settings,
            raw_bytes=_directory_size(settings.raw_dir),
            reserve_bytes=reserve_bytes,
            temporary_size_factor=temporary_size_factor,
        )

        logger.info("Starting acquisition: force=%s", force_download)
        artifacts = downloader.download_all(
            DATASETS,
            raw_dir=settings.raw_dir,
            manifest_path=settings.manifest_path,
            force=force_download,
        )
        raw_bytes = sum(artifact.manifest_entry.size_bytes for artifact in artifacts)
        logger.info("Completed acquisition: files=%d bytes=%d", len(artifacts), raw_bytes)

        _check_space(
            settings,
            raw_bytes=raw_bytes,
            reserve_bytes=reserve_bytes,
            temporary_size_factor=temporary_size_factor,
        )
        removed = prune_obsolete_builds(settings.lakehouse_dir, keep_retired=1)
        if removed:
            logger.info("Pruned obsolete lakehouse directories: count=%d", len(removed))

        paths = BuildPaths.create(settings.lakehouse_dir)
        with temporary_build(paths):
            logger.info("Starting ingestion: build=%s", paths.build_id)
            ingestion = ingest_snapshot(
                artifacts,
                build_paths=paths,
                pipelines_dir=settings.dlt_pipelines_dir,
                show_progress=show_progress,
            )

            logger.info("Starting dbt build: build=%s", paths.build_id)
            transformation = run_dbt(
                ("build",),
                build_paths=paths,
                project_dir=settings.dbt_project_dir,
                profiles_dir=settings.dbt_project_dir,
                controller_path=settings.dbt_state_dir / f"{paths.build_id}.duckdb",
                executable=dbt_executable,
                environment=environment,
            )

            logger.info("Starting fresh-process validation: build=%s", paths.build_id)
            validation = validate_build(
                paths,
                executable=python_executable,
                environment=environment,
                working_directory=settings.repository_root,
            )

            logger.info("Promoting validated build: build=%s", paths.build_id)
            promoted = promote_build(paths)

    logger.info(
        "Completed lakehouse build: build=%s elapsed_seconds=%.2f current=%s",
        promoted.build_id,
        time.monotonic() - started,
        promoted.current_dir,
    )
    return BuildResult(artifacts, ingestion, transformation, validation, promoted)


def _check_space(
    settings: Settings,
    *,
    raw_bytes: int,
    reserve_bytes: int,
    temporary_size_factor: int,
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
    logger.info(
        "Free-space gate passed: required_bytes=%d available_bytes=%d",
        budget.required_bytes,
        free_bytes,
    )


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    try:
        return sum(item.stat().st_size for item in path.rglob("*") if item.is_file())
    except OSError as error:
        raise LifecycleError(f"Could not measure disk usage for {path}") from error
