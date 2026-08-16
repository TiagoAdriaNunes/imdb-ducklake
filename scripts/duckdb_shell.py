"""Interactive DuckDB SQL shell, read-only attached to the current promoted lakehouse."""

from __future__ import annotations

import sys

import duckdb

from imdb_ducklake.config import Settings
from imdb_ducklake.exceptions import ImdbLakehouseError


def main() -> None:
    # duckdb's table rendering uses box-drawing characters that the default Windows console
    # codepage (cp1252) can't encode, especially when stdout isn't a real TTY.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    settings = Settings.load()
    catalog_path = settings.current_dir / "catalog.duckdb"
    storage_dir = settings.current_dir / "storage"
    if not catalog_path.is_file():
        raise SystemExit(f"No promoted build found at {catalog_path}")

    connection = duckdb.connect(":memory:")
    connection.execute("INSTALL ducklake")
    connection.execute("LOAD ducklake")
    connection.execute(
        f"ATTACH 'ducklake:{catalog_path.as_posix()}' AS imdb_lake "
        f"(DATA_PATH '{storage_dir.as_posix()}', OVERRIDE_DATA_PATH true, READ_ONLY)"
    )
    connection.execute("USE imdb_lake")
    print(f"Attached read-only: {catalog_path}")
    print("Type SQL ending in ';' to run it. .exit or Ctrl-D to quit.\n")

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


if __name__ == "__main__":
    try:
        main()
    except ImdbLakehouseError as error:
        raise SystemExit(f"Error: {error}") from error
