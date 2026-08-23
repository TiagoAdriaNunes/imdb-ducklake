"""Explicit dlt resources for verified IMDb source archives."""

from __future__ import annotations

from collections.abc import Iterable, Iterator
from dataclasses import asdict
from pathlib import Path
from typing import Any

import dlt
from dlt.common.schema.typing import TColumnSchema
from dlt.extract.resource import DltResource

from imdb_ducklake.acquisition.downloader import VerifiedArtifact
from imdb_ducklake.datasets import DatasetSpec
from imdb_ducklake.exceptions import IngestionError

_NO_NULL_SENTINEL = "__IMDB_DUCKLAKE_NO_NULL_VALUE__"


def _read_csv_duckdb_arrow(
    path: Path,
    *,
    chunk_size: int,
    **duckdb_options: Any,
) -> Iterator[Any]:
    """Stream one CSV file as Arrow batches through DuckDB's current reader API.

    Reads the path with ``compression="gzip"`` instead of a file object: a
    file object routes through DuckDB's PythonFilesystem, which buffers the
    whole decompressed file in memory first and OOMs on multi-GB archives.

    Opens its own connection rather than using DuckDB's implicit shared default
    connection: the caller runs this as a ``parallelized`` resource, so concurrent
    extraction of two archives on the shared default connection would corrupt or
    fail each other's queries.
    """
    import duckdb

    connection = duckdb.connect(":memory:")
    try:
        relation = connection.read_csv(str(path), compression="gzip", **duckdb_options)
        yield from relation.to_arrow_reader(batch_size=chunk_size)
    finally:
        connection.close()


def build_ingestion_resources(
    artifacts: Iterable[VerifiedArtifact],
    *,
    chunk_size: int = 50_000,
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
    """Build one top-level parallelized resource that reads exactly one known file.

    Deliberately not a `filesystem() | transformer(parallelized=True)` pipe (dlt's
    documented pattern for reading local files): that fork/transformer shape only
    discovers its wrapped, poolable generator *after* the parent's single item has
    been forked into it mid-run, and empirically that delayed discovery starves
    round-robin scheduling across resources - verified two archives extracted
    through it never overlapped even with `extract.workers > 1`. A plain top-level
    `@dlt.resource(parallelized=True)` generator is present in the pool from the
    first iteration and genuinely overlaps with sibling resources.
    """
    _validate_artifact(artifact)
    dataset = artifact.dataset
    path = artifact.path

    @dlt.resource(
        name=f"read_{dataset.table_name}",
        table_name=dataset.table_name,
        write_disposition="replace",
        columns=_raw_columns(dataset),
        schema_contract={"columns": "freeze", "data_type": "freeze"},
        parallelized=True,
    )
    def read_csv() -> Iterator[Any]:
        yield from _read_csv_duckdb_arrow(
            path,
            chunk_size=chunk_size,
            delimiter="\t",
            header=True,
            all_varchar=True,
            columns={header: "VARCHAR" for header in dataset.headers},
            na_values=_NO_NULL_SENTINEL,
            quotechar="",
            escapechar="",
        )

    return read_csv()


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
