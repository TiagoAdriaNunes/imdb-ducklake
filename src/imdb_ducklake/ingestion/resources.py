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


def build_ingestion_resources(
    artifacts: Iterable[VerifiedArtifact],
) -> tuple[DltResource, ...]:
    """Build one lossless raw resource per archive plus load-level file metadata."""
    verified = tuple(artifacts)
    if not verified:
        raise IngestionError("At least one verified source artifact is required")
    table_names = [artifact.dataset.table_name for artifact in verified]
    if len(set(table_names)) != len(table_names):
        raise IngestionError("Verified source artifacts contain duplicate table names")

    resources = tuple(_raw_resource(artifact) for artifact in verified)
    return (*resources, _manifest_resource(verified))


def _raw_resource(artifact: VerifiedArtifact) -> DltResource:
    """Build one resource that loads exactly one archive as a dlt SQL model.

    The resource yields a query, not rows: dlt's loader runs it inside the DuckLake connection
    as ``INSERT INTO raw.<table> SELECT ... FROM read_csv(...)``, so the archive is read once
    and written once, with no Arrow batches or intermediate files. dlt still creates and
    replaces the table, enforces the frozen columns, and adds ``_dlt_load_id``. ADR 0015.
    """
    _validate_artifact(artifact)
    dataset = artifact.dataset
    query = _read_csv_select(dataset, artifact.path)

    @dlt.resource(
        name=f"read_{dataset.table_name}",
        table_name=dataset.table_name,
        write_disposition="replace",
        columns=_raw_columns(dataset),
        schema_contract={"columns": "freeze", "data_type": "freeze"},
        file_format="model",
    )
    def read_csv() -> Iterator[Any]:
        yield dlt.current.pipeline().dataset()(query)

    return read_csv()


def _read_csv_select(dataset: DatasetSpec, path: Path) -> str:
    """Return a DuckDB SELECT reading one gzip TSV as all-VARCHAR columns, losslessly.

    No quoting, escaping, or NULL conversion: ``\\N`` and empty strings stay literal text.
    """
    columns = ", ".join(_quote_identifier(header) for header in dataset.headers)
    types = ", ".join(f"{_quote_literal(header)}: 'VARCHAR'" for header in dataset.headers)
    return (
        f"SELECT {columns} FROM read_csv({_quote_literal(path.as_posix())}, "
        "delim = '\\t', header = true, all_varchar = true, quote = '', escape = '', "
        f"nullstr = {_quote_literal(_NO_NULL_SENTINEL)}, compression = 'gzip', "
        f"columns = {{{types}}})"
    )


def _quote_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _quote_identifier(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


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
