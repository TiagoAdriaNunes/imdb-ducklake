"""Lossless dlt ingestion into a local DuckLake build."""

from imdb_ducklake.ingestion.pipeline import IngestionResult, ingest_snapshot
from imdb_ducklake.ingestion.resources import build_ingestion_resources

__all__ = ["IngestionResult", "build_ingestion_resources", "ingest_snapshot"]
