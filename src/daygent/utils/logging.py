"""Minimal logging helpers. No telemetry."""

from __future__ import annotations

import logging

LOGGER_NAME = "daygent"


def get_logger() -> logging.Logger:
    """Return the Daygent logger."""
    return logging.getLogger(LOGGER_NAME)


def configure_logging(verbose: bool = False) -> None:
    """Configure stderr logging. Verbose enables DEBUG diagnostics."""
    logger = get_logger()
    if logger.handlers:
        logger.setLevel(logging.DEBUG if verbose else logging.INFO)
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG if verbose else logging.INFO)
    logger.propagate = False
