"""Fresh-process, read-only validation for a completed DuckLake build."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path

from imdb_ducklake.exceptions import ValidationError
from imdb_ducklake.lakehouse.catalog import CatalogTarget
from imdb_ducklake.lakehouse.lifecycle import BuildPaths

ProcessRunner = Callable[
    [Sequence[str], Path, Mapping[str, str]],
    subprocess.CompletedProcess[str],
]

REQUIRED_RELATIONS: dict[str, frozenset[str]] = {
    "raw": frozenset(
        {
            "ingestion_files",
            "name_basics",
            "title_akas",
            "title_basics",
            "title_crew",
            "title_episode",
            "title_principals",
            "title_ratings",
        }
    ),
    "staging": frozenset(
        {
            "stg_imdb__ingestion_files",
            "stg_imdb__name_basics",
            "stg_imdb__title_akas",
            "stg_imdb__title_basics",
            "stg_imdb__title_crew",
            "stg_imdb__title_episode",
            "stg_imdb__title_principals",
            "stg_imdb__title_ratings",
        }
    ),
    "intermediate": frozenset(
        {
            "bridge_title_akas",
            "bridge_title_credits",
            "bridge_title_crew",
            "bridge_title_genres",
            "dim_people",
            "dim_titles",
            "fct_episodes",
            "fct_title_ratings",
            "int_title_director_lists",
            "int_title_genre_lists",
        }
    ),
    "marts": frozenset(
        {
            "mart_genre_year_summary",
            "mart_person_filmography",
            "mart_series_episodes",
            "mart_title_search",
        }
    ),
}


@dataclass(frozen=True, slots=True)
class ValidationResult:
    """Summary returned after an independent process validates one build."""

    build_id: str
    relation_count: int
    mart_row_counts: dict[str, int]


def validate_build(
    build_paths: BuildPaths,
    *,
    executable: str,
    environment: Mapping[str, str],
    working_directory: Path,
    runner: ProcessRunner | None = None,
    catalog_target: CatalogTarget | None = None,
) -> ValidationResult:
    """Validate a build through a separate Python process and a read-only attachment."""
    if catalog_target is not None:
        if not catalog_target.storage_dir.is_dir():
            raise ValidationError(f"DuckLake storage does not exist: {catalog_target.storage_dir}")
        command = (
            executable,
            "-m",
            "imdb_ducklake.lakehouse.validation",
            "--storage",
            str(catalog_target.storage_dir),
            "--build-id",
            build_paths.build_id,
            "--metadata-schema",
            catalog_target.metadata_schema,
        )
        child_environment = dict(environment)
        child_environment["IMDB_DUCKLAKE_VALIDATION_CATALOG"] = catalog_target.duckdb_metadata_path
        return _execute_validation(
            command, build_paths.build_id, working_directory, child_environment, runner
        )
    return validate_catalog(
        catalog_path=build_paths.catalog_path,
        storage_dir=build_paths.storage_dir,
        build_id=build_paths.build_id,
        executable=executable,
        environment=environment,
        working_directory=working_directory,
        runner=runner,
    )


def validate_catalog(
    *,
    catalog_path: Path,
    storage_dir: Path,
    build_id: str,
    executable: str,
    environment: Mapping[str, str],
    working_directory: Path,
    runner: ProcessRunner | None = None,
) -> ValidationResult:
    """Validate explicit catalog and storage paths through a separate read-only process."""
    if not catalog_path.is_file() or not storage_dir.is_dir():
        raise ValidationError(
            f"DuckLake build is incomplete: catalog={catalog_path}, storage={storage_dir}"
        )
    command = (
        executable,
        "-m",
        "imdb_ducklake.lakehouse.validation",
        "--catalog",
        str(catalog_path),
        "--storage",
        str(storage_dir),
        "--build-id",
        build_id,
    )
    return _execute_validation(command, build_id, working_directory, dict(environment), runner)


def _execute_validation(
    command: Sequence[str],
    build_id: str,
    working_directory: Path,
    environment: Mapping[str, str],
    runner: ProcessRunner | None,
) -> ValidationResult:
    try:
        completed = (runner or _run_process)(command, working_directory.resolve(), environment)
    except OSError as error:
        raise ValidationError("Could not start the fresh-process validation gate") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()
        if len(detail) > 2_000:
            detail = detail[-2_000:]
        raise ValidationError(
            f"Fresh-process validation failed with exit code {completed.returncode}: {detail}"
        )
    try:
        payload = json.loads(completed.stdout)
        return ValidationResult(
            build_id=str(payload["build_id"]),
            relation_count=int(payload["relation_count"]),
            mart_row_counts={
                str(name): int(row_count) for name, row_count in payload["mart_row_counts"].items()
            },
        )
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as error:
        raise ValidationError("Fresh-process validation returned an invalid result") from error


def _run_process(
    command: Sequence[str],
    cwd: Path,
    environment: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )


def _validate_read_only(
    catalog: str, storage_dir: Path, build_id: str, metadata_schema: str = "main"
) -> ValidationResult:
    try:
        import duckdb

        connection = duckdb.connect(":memory:")
        try:
            connection.execute("LOAD ducklake")
            catalog_value = str(catalog)
            metadata_path = (
                catalog_value
                if catalog_value.startswith("postgres:")
                else Path(catalog_value).resolve().as_posix()
            )
            catalog_sql = _sql_string(f"ducklake:{metadata_path}")
            storage = _sql_string(storage_dir.resolve().as_posix())
            schema = _sql_string(metadata_schema)
            connection.execute(
                f"ATTACH {catalog_sql} AS imdb_lake "
                f"(DATA_PATH {storage}, METADATA_SCHEMA {schema}, "
                "OVERRIDE_DATA_PATH true, READ_ONLY)"
            )
            rows = connection.execute(
                """
                select table_schema, table_name
                from information_schema.tables
                where table_catalog = 'imdb_lake'
                """
            ).fetchall()
            actual = {(str(schema), str(relation)) for schema, relation in rows}
            missing = sorted(
                f"{schema}.{relation}"
                for schema, relations in REQUIRED_RELATIONS.items()
                for relation in relations
                if (schema, relation) not in actual
            )
            if missing:
                raise ValidationError(
                    f"Required DuckLake relations are missing: {', '.join(missing)}"
                )

            mart_row_counts = {}
            for relation in sorted(REQUIRED_RELATIONS["marts"]):
                count_row = connection.execute(
                    f'SELECT count(*) FROM imdb_lake.marts."{relation}"'
                ).fetchone()
                if count_row is None:
                    raise ValidationError(f"Could not count rows in marts.{relation}")
                mart_row_counts[relation] = int(count_row[0])
            representative_queries = (
                """
                select tconst, primary_title, average_rating, genres, directors
                from imdb_lake.marts.mart_title_search limit 1
                """,
                """
                select genre, start_year, title_count, rated_title_count, total_votes
                from imdb_lake.marts.mart_genre_year_summary limit 1
                """,
                """
                select nconst, tconst, category, characters
                from imdb_lake.marts.mart_person_filmography limit 1
                """,
                """
                select series_tconst, episode_tconst, season_number, episode_number
                from imdb_lake.marts.mart_series_episodes limit 1
                """,
            )
            for query in representative_queries:
                connection.execute(query).fetchall()
        finally:
            connection.close()
    except ValidationError:
        raise
    except Exception as error:
        raise ValidationError(
            "Could not query the build through a read-only DuckLake attachment: "
            f"{type(error).__name__}: {error}"
        ) from error
    return ValidationResult(
        build_id=build_id,
        relation_count=sum(len(relations) for relations in REQUIRED_RELATIONS.values()),
        mart_row_counts=mart_row_counts,
    )


def _sql_string(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _worker_main(arguments: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--catalog")
    parser.add_argument("--storage", type=Path, required=True)
    parser.add_argument("--build-id", required=True)
    parser.add_argument("--metadata-schema", default="main")
    options = parser.parse_args(arguments)
    try:
        catalog = options.catalog or os.environ.get("IMDB_DUCKLAKE_VALIDATION_CATALOG")
        if not catalog:
            raise ValidationError("DuckLake validation catalog is not configured")
        result = _validate_read_only(
            catalog, options.storage, options.build_id, options.metadata_schema
        )
    except ValidationError as error:
        print(str(error), file=sys.stderr)
        return 1
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(_worker_main())
