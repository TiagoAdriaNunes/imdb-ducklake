"""Run a complete verified IMDb snapshot through dlt into DuckLake."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import dlt
from dlt.common.runtime.collector_base import Collector
from dlt.common.schema import Schema
from dlt.destinations import ducklake
from dlt.destinations.impl.ducklake.configuration import DuckLakeCredentials
from loguru import logger

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import IngestionError
from imdb_ducklake.ingestion.progress import RichProgressCollector
from imdb_ducklake.ingestion.resources import build_ingestion_resources
from imdb_ducklake.lakehouse.lifecycle import BuildPaths


@dataclass(frozen=True, slots=True)
class IngestionResult:
    """Stable application-facing summary of a completed dlt load."""

    pipeline_name: str
    dataset_name: str
    load_ids: tuple[str, ...]
    catalog_path: Path
    storage_dir: Path


def ingest_snapshot(
    artifacts: tuple[VerifiedArtifact, ...],
    *,
    build_paths: BuildPaths,
    pipelines_dir: Path,
    chunk_size: int = 5_000,
    progress: Collector | None = None,
) -> IngestionResult:
    """Replace the raw schema with one complete seven-file IMDb snapshot."""
    _validate_complete_snapshot(artifacts)
    if not build_paths.temporary_dir.is_dir() or not build_paths.storage_dir.is_dir():
        raise IngestionError(f"Build workspace is not initialized: {build_paths.temporary_dir}")
    pipeline_name = _pipeline_name(build_paths.build_id)
    pipelines_dir.mkdir(parents=True, exist_ok=True)
    credentials = DuckLakeCredentials(
        ducklake_name="imdb_lake",
        catalog=f"duckdb:///{build_paths.catalog_path.as_posix()}",
        storage=build_paths.storage_dir.as_uri(),
    )
    destination = ducklake(
        credentials=credentials,
        destination_name="imdb_lake",
        local_dir=str(build_paths.temporary_dir),
    )
    schema = Schema("raw", normalizers={"names": "direct"})
    resources = build_ingestion_resources(artifacts, chunk_size=chunk_size)
    total_bytes = sum(artifact.manifest_entry.size_bytes for artifact in artifacts)
    ingestion_logger = logger.bind(build_id=build_paths.build_id, stage="ingestion")
    ingestion_logger.info(
        "DLT ingestion started",
        event_code="dlt_ingestion_started",
        status="started",
        files=len(artifacts),
        compressed_bytes=total_bytes,
        catalog=str(build_paths.catalog_path),
    )
    for artifact in artifacts:
        ingestion_logger.info(
            "DLT resource queued",
            event_code="dlt_resource_queued",
            status="queued",
            dataset=artifact.dataset.table_name,
            file_name=artifact.path.name,
            compressed_bytes=artifact.manifest_entry.size_bytes,
        )
    pipeline_options: dict[str, Any] = {}
    if progress is not None:
        if isinstance(progress, RichProgressCollector):
            progress.set_expected_totals(
                {
                    artifact.dataset.table_name: artifact.manifest_entry.row_count
                    for artifact in artifacts
                    if artifact.manifest_entry.row_count is not None
                }
            )
        pipeline_options["progress"] = progress
    started = time.monotonic()

    try:
        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            pipelines_dir=str(pipelines_dir.resolve()),
            destination=destination,
            dataset_name="raw",
            **pipeline_options,
        )
        with dlt.config.values(
            {
                "load.workers": 1,
                "normalize.workers": 2,
                "normalize.parquet_normalizer.add_dlt_load_id": True,
            }
        ):
            load_info = pipeline.run(
                resources,
                schema=schema,
                refresh="drop_data",
                loader_file_format="parquet",
            )
        if load_info.has_failed_jobs:
            raise IngestionError(load_info.asstr(verbosity=1))
    except IngestionError:
        raise
    except Exception as error:
        detail = f"{type(error).__name__}: {error}" if str(error) else type(error).__name__
        raise IngestionError(
            f"dlt could not load IMDb snapshot into {build_paths.catalog_path}: {detail}"
        ) from error

    ingestion_logger.info(
        "DLT ingestion completed",
        event_code="dlt_ingestion_completed",
        status="completed",
        loads=len(load_info.loads_ids),
        dlt_load_ids=list(load_info.loads_ids),
        elapsed_seconds=round(time.monotonic() - started, 2),
    )

    return IngestionResult(
        pipeline_name=pipeline_name,
        dataset_name="raw",
        load_ids=tuple(load_info.loads_ids),
        catalog_path=build_paths.catalog_path,
        storage_dir=build_paths.storage_dir,
    )


def _validate_complete_snapshot(artifacts: tuple[VerifiedArtifact, ...]) -> None:
    expected = {dataset.table_name for dataset in DATASETS}
    actual = {artifact.dataset.table_name for artifact in artifacts}
    if len(artifacts) != len(DATASETS) or actual != expected:
        missing = sorted(expected - actual)
        unexpected = sorted(actual - expected)
        raise IngestionError(
            "A complete IMDb snapshot requires all seven datasets; "
            f"missing={missing}, unexpected={unexpected}"
        )


def _pipeline_name(build_id: str) -> str:
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", build_id)
    return f"imdb_{normalized}"
