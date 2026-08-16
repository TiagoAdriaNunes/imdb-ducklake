"""Launch the real DuckDB CLI, read-only attached to the current promoted lakehouse.

Falls back to a minimal Python REPL if the DuckDB CLI isn't installed
(https://duckdb.org/install), since the `duckdb` PyPI package doesn't bundle it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import duckdb

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import ImdbLakehouseError

_ATTACH_ALIAS = "imdb_lake"


def _attach_sql(catalog_path: Path, storage_dir: Path) -> str:
    return (
        "INSTALL ducklake;\n"
        "LOAD ducklake;\n"
        f"ATTACH 'ducklake:{catalog_path.as_posix()}' AS {_ATTACH_ALIAS} "
        f"(DATA_PATH '{storage_dir.as_posix()}', OVERRIDE_DATA_PATH true, READ_ONLY);\n"
        f"USE {_ATTACH_ALIAS};\n"
    )


def _run_real_cli(executable: str, attach_sql: str) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sql", delete=False, encoding="utf-8"
    ) as init_file:
        init_file.write(attach_sql)
        init_path = init_file.name
    try:
        result = subprocess.run([executable, "-init", init_path], check=False)
    finally:
        os.unlink(init_path)
    raise SystemExit(result.returncode)


def _run_python_fallback(attach_sql: str) -> None:
    # duckdb's table rendering uses box-drawing characters that the default Windows console
    # codepage (cp1252) can't encode, especially when stdout isn't a real TTY.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    connection = duckdb.connect(":memory:")
    for statement in attach_sql.strip().split(";"):
        if statement.strip():
            connection.execute(statement)
    print("DuckDB CLI not found (https://duckdb.org/install) - using a minimal Python fallback.")
    print(f"Attached read-only as '{_ATTACH_ALIAS}'. SQL ending in ';' runs it; .exit to quit.\n")

    buffer = ""
    while True:
        try:
            line = input("   ...> " if buffer else "duckdb> ")
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not buffer and line.strip() in {".exit", ".quit"}:
            break
        buffer += line + "\n"
        if buffer.rstrip().endswith(";"):
            try:
                connection.sql(buffer).show()
            except duckdb.Error as error:
                print(f"Error: {error}")
            buffer = ""


def main() -> None:
    settings = Settings.load()
    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    if not catalog_path.is_file():
        raise SystemExit(f"No promoted build found at {catalog_path}")

    attach_sql = _attach_sql(catalog_path, storage_dir)
    executable = shutil.which("duckdb")
    if executable:
        _run_real_cli(executable, attach_sql)
    else:
        _run_python_fallback(attach_sql)


if __name__ == "__main__":
    try:
        main()
    except ImdbLakehouseError as error:
        raise SystemExit(f"Error: {error}") from error
