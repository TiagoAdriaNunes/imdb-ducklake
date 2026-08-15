"""Run a complete verified IMDb snapshot through dlt into DuckLake."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import dlt
from dlt.common.schema import Schema
from dlt.destinations import ducklake
from dlt.destinations.impl.ducklake.configuration import DuckLakeCredentials

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.datasets import DATASETS
from imdb_ducklake.exceptions import IngestionError
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

    try:
        pipeline = dlt.pipeline(
            pipeline_name=pipeline_name,
            pipelines_dir=str(pipelines_dir.resolve()),
            destination=destination,
            dataset_name="raw",
        )
        with dlt.config.values(
            {
                "load.workers": 1,
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
        raise IngestionError(
            f"dlt could not load IMDb snapshot into {build_paths.catalog_path}"
        ) from error

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
