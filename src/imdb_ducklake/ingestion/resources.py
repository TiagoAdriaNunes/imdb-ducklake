"""Explicit dlt resources for verified IMDb source archives."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict
from typing import Any

import dlt
from dlt.common.schema.typing import TColumnSchema
from dlt.common.storages.fsspec_filesystem import FileItemDict
from dlt.extract.resource import DltResource
from dlt.sources.filesystem import filesystem

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import IngestionError

_NO_NULL_SENTINEL = "__IMDB_DUCKLAKE_NO_NULL_VALUE__"


def _read_csv_duckdb_arrow(
    items: Iterable[FileItemDict],
    /,
    *,
    chunk_size: int,
    **duckdb_options: Any,
) -> Iterator[Any]:
    """Stream CSV files as Arrow batches through DuckDB's current reader API."""
    import duckdb

    for item in items:
        with item.open() as file_object:
            relation = duckdb.from_csv_auto(file_object, **duckdb_options)
            yield from relation.to_arrow_reader(batch_size=chunk_size)


read_csv_duckdb_arrow = dlt.transformer()(_read_csv_duckdb_arrow)


def build_ingestion_resources(
    artifacts: Iterable[VerifiedArtifact],
    *,
    chunk_size: int = 5_000,
) -> tuple[DltResource, ...]:
    """Build one lossless raw resource per archive plus load-level file metadata."""
    if chunk_size < 1:
        raise ValueError("chunk_size must be at least one")
    verified = tuple(artifacts)
    if not verified:
        raise IngestionError("At least one verified source artifact is required")
    table_names = [artifact.dataset.table_name for artifact in verified]
    if len(set(table_names)) != len(table_names):
        raise IngestionError("Verified source artifacts contain duplicate table names")

    resources = tuple(_raw_resource(artifact, chunk_size=chunk_size) for artifact in verified)
    return (*resources, _manifest_resource(verified))


def _raw_resource(artifact: VerifiedArtifact, *, chunk_size: int) -> DltResource:
    _validate_artifact(artifact)
    dataset = artifact.dataset
    files = filesystem(
        bucket_url=artifact.path.parent.resolve().as_uri(),
        file_glob=artifact.path.name,
        files_per_page=1,
    ).with_name(f"files_{dataset.table_name}")
    reader = files | read_csv_duckdb_arrow(
        chunk_size=chunk_size,
        delimiter="\t",
        header=True,
        all_varchar=True,
        columns={header: "VARCHAR" for header in dataset.headers},
        na_values=_NO_NULL_SENTINEL,
        quotechar="",
        escapechar="",
    )
    reader.with_name(f"read_{dataset.table_name}")
    reader.apply_hints(
        table_name=dataset.table_name,
        write_disposition="replace",
        columns=_raw_columns(dataset),
        schema_contract={"columns": "freeze", "data_type": "freeze"},
    )
    return reader


def _manifest_resource(artifacts: tuple[VerifiedArtifact, ...]) -> DltResource:
    rows = [asdict(artifact.manifest_entry) for artifact in artifacts]

    @dlt.resource(
        name="ingestion_files",
        table_name="ingestion_files",
        write_disposition="replace",
        columns=[
            {"name": "dataset", "data_type": "text", "nullable": False},
            {"name": "file_name", "data_type": "text", "nullable": False},
            {"name": "table_name", "data_type": "text", "nullable": False},
            {"name": "url", "data_type": "text", "nullable": False},
            {"name": "size_bytes", "data_type": "bigint", "nullable": False},
            {"name": "row_count", "data_type": "bigint", "nullable": True},
            {"name": "sha256", "data_type": "text", "nullable": False},
            {"name": "downloaded_at", "data_type": "text", "nullable": False},
            {"name": "batch_id", "data_type": "text", "nullable": False},
            {"name": "etag", "data_type": "text", "nullable": True},
            {"name": "last_modified", "data_type": "text", "nullable": True},
            {"name": "content_type", "data_type": "text", "nullable": True},
        ],
        schema_contract={"columns": "freeze", "data_type": "freeze"},
    )
    def ingestion_files() -> Iterable[dict[str, Any]]:
        yield from rows

    return ingestion_files()


def _raw_columns(dataset: DatasetSpec) -> list[TColumnSchema]:
    return [
        TColumnSchema(name=header, data_type="text", nullable=True) for header in dataset.headers
    ]


def _validate_artifact(artifact: VerifiedArtifact) -> None:
    entry = artifact.manifest_entry
    if not artifact.path.is_file():
        raise IngestionError(f"Verified source archive does not exist: {artifact.path}")
    if (
        artifact.path.name != artifact.dataset.file_name
        or entry.file_name != artifact.dataset.file_name
        or entry.table_name != artifact.dataset.table_name
        or entry.url != artifact.dataset.url
    ):
        raise IngestionError(
            f"Verified artifact metadata does not match {artifact.dataset.table_name}"
        )
