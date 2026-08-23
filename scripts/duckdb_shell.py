"""Launch the real DuckDB CLI, read-only attached to the current promoted lakehouse.

Falls back to a minimal Python REPL if the DuckDB CLI isn't installed
(https://duckdb.org/install), since the `duckdb` PyPI package doesn't bundle it.

Pass --ui to open DuckDB's local web UI instead of/alongside the terminal shell.
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
from imdb_ducklake.query.service import ATTACH_ALIAS, configured_attach_sql

# `winget install <id>` always lands packages under this fixed, non-machine-specific suffix
# (the Microsoft Store publisher ID for winget-manifest installs), and appends it to the User
# PATH registry value - but an already-running terminal keeps the environment it started with,
# so `shutil.which` alone can miss a `winget install DuckDB.cli` done in a different, newer
# session. Check the standard location as a fallback before giving up.
_WINGET_DUCKDB_PATH = (
    Path(os.environ.get("LOCALAPPDATA", ""))
    / "Microsoft"
    / "WinGet"
    / "Packages"
    / "DuckDB.cli_Microsoft.Winget.Source_8wekyb3d8bbwe"
    / "duckdb.exe"
)


def _find_duckdb() -> str | None:
    found = shutil.which("duckdb")
    if found:
        return found
    if _WINGET_DUCKDB_PATH.is_file():
        return str(_WINGET_DUCKDB_PATH)
    return None


def _run_real_cli(executable: str, attach_sql: str, *, ui: bool) -> None:
    with tempfile.NamedTemporaryFile(
        "w", suffix=".sql", delete=False, encoding="utf-8"
    ) as init_file:
        init_file.write(attach_sql)
        init_path = init_file.name
    command = [executable, "-init", init_path]
    if ui:
        command.append("-ui")
    try:
        result = subprocess.run(command, check=False)
    finally:
        os.unlink(init_path)
    raise SystemExit(result.returncode)


def _run_python_fallback(attach_sql: str, *, ui: bool) -> None:
    # duckdb's table rendering uses box-drawing characters that the default Windows console
    # codepage (cp1252) can't encode, especially when stdout isn't a real TTY.
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    connection = duckdb.connect(":memory:")
    for statement in attach_sql.strip().split(";"):
        if statement.strip():
            connection.execute(statement)
    print("DuckDB CLI not found (https://duckdb.org/install) - using a minimal Python fallback.")
    if ui:
        connection.execute("INSTALL ui")
        connection.execute("LOAD ui")
        connection.execute("CALL start_ui()")
        print("UI started in your browser (CALL stop_ui_server(); to close it).")
    print(f"Attached read-only as '{ATTACH_ALIAS}'. SQL ending in ';' runs it; .exit to quit.\n")

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
    ui = "--ui" in sys.argv[1:]
    settings = Settings.load()
    attach_sql_str = configured_attach_sql(settings)
    executable = _find_duckdb()
    if executable:
        _run_real_cli(executable, attach_sql_str, ui=ui)
    else:
        _run_python_fallback(attach_sql_str, ui=ui)


if __name__ == "__main__":
    try:
        main()
    except ImdbLakehouseError as error:
        raise SystemExit(f"Error: {error}") from error
