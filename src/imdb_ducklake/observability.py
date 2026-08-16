"""Loguru configuration shared by command-line entry points."""

from __future__ import annotations

import logging
import sys
from types import FrameType
from typing import TYPE_CHECKING, Any, TextIO
from uuid import uuid4

from loguru import logger
from rich.console import Console
from rich.text import Text

if TYPE_CHECKING:
    from loguru import Record

_HIDDEN_CONSOLE_FIELDS = frozenset({"event_code", "logger_name", "stage", "status"})
_STANDARD_RECORD_FIELDS = frozenset({*logging.makeLogRecord({}).__dict__, "asctime", "message"})
_console = Console(stderr=True)
_active_log_format = "console"


def _format_bytes(value: int) -> str:
    """Render byte counts compactly for interactive console output."""
    size = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if abs(size) < 1024 or unit == "TiB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024
    raise AssertionError("unreachable")


def _format_console_value(value: Any) -> str:
    if isinstance(value, bool):
        return str(value).lower()
    return str(value)


def _console_format(record: Record) -> str:
    """Build a compact console line from Loguru's structured record."""
    fields: list[str] = []
    correlations: list[str] = []
    for key, value in record["extra"].items():
        if key in _HIDDEN_CONSOLE_FIELDS:
            continue
        if key in {"run_id", "build_id"}:
            short_key = key.removesuffix("_id")
            correlations.append(f"{short_key}={str(value)[:8]}")
            continue
        if key.endswith("_bytes") and isinstance(value, int):
            fields.append(f"{key.removesuffix('_bytes')}={_format_bytes(value)}")
            continue
        fields.append(f"{key}={_format_console_value(value)}")

    context = " | " + " ".join([*fields, *correlations]) if fields or correlations else ""
    record["extra"]["console_context"] = context
    return (
        "<green>{time:HH:mm:ss}</green> | <level>{level}</level> | "
        "<level>{message}</level>{extra[console_context]}\n{exception}"
    )


def _console_sink(message: Any) -> None:
    """Write Loguru messages through Rich so live progress remains intact."""
    _console.print(Text.from_ansi(str(message)), end="")


class _InterceptHandler(logging.Handler):
    """Route standard-library and dependency records through Loguru."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            level: str | int = logger.level(record.levelname).name
        except ValueError:
            level = record.levelno

        frame: FrameType | None = logging.currentframe()
        depth = 0
        while frame is not None and (depth == 0 or frame.f_code.co_filename == logging.__file__):
            frame = frame.f_back
            depth += 1

        extra = {
            key: value
            for key, value in record.__dict__.items()
            if key not in _STANDARD_RECORD_FIELDS
        }
        logger.bind(logger_name=record.name, **extra).opt(
            depth=depth,
            exception=record.exc_info,
        ).log(
            level,
            record.getMessage(),
        )


def configure_logging(
    level: str,
    log_format: str = "console",
    *,
    stream: TextIO | None = None,
) -> None:
    """Configure Loguru and route standard-library records through it."""
    global _active_log_format, _console

    if log_format not in {"console", "json"}:
        raise ValueError(f"Unsupported log format: {log_format}")

    output = stream or sys.stderr
    is_terminal = bool(getattr(output, "isatty", lambda: False)())
    _active_log_format = log_format
    _console = Console(
        file=output,
        force_terminal=is_terminal,
        color_system="auto" if is_terminal else None,
    )
    logger.remove()
    logger.configure(extra={})
    if log_format == "json":
        logger.add(
            output,
            level=level,
            serialize=True,
            backtrace=False,
            diagnose=False,
        )
    else:
        logger.add(
            _console_sink,
            level=level,
            format=_console_format,
            colorize=is_terminal,
            backtrace=False,
            diagnose=False,
        )

    logging.basicConfig(
        handlers=[_InterceptHandler()],
        level=level,
        force=True,
    )


def get_console() -> Console:
    """Return the console shared by Loguru and interactive progress displays."""
    return _console


def rich_progress_enabled() -> bool:
    """Return whether this process should render an interactive Rich display."""
    return _active_log_format == "console" and _console.is_terminal


def start_run_context(run_id: str | None = None) -> str:
    """Set one correlation identifier for this CLI execution."""
    selected_run_id = run_id or str(uuid4())
    logger.configure(extra={"run_id": selected_run_id})
    return selected_run_id
