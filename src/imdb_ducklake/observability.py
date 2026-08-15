"""Logging setup shared by command-line entry points."""

import logging


def configure_logging(level: str) -> None:
    """Configure predictable process-level logging."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
        force=True,
    )
