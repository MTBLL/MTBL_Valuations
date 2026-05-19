"""Logging configuration for the MTBL valuation engine.

The package logs under the ``mtbl_valuations`` namespace. Call
:func:`configure_logging` once at CLI startup; every module then obtains a
child logger via :func:`get_logger` and inherits that level and handler.
"""

from __future__ import annotations

import logging

PACKAGE_LOGGER = "mtbl_valuations"

# -v count -> level. 0 is the default (quiet); each extra -v steps down.
_VERBOSITY_LEVELS = {
    0: logging.WARNING,
    1: logging.INFO,
    2: logging.DEBUG,
}


def configure_logging(verbosity: int = 0, log_level: str | None = None) -> logging.Logger:
    """Configure the ``mtbl_valuations`` package logger.

    Args:
        verbosity: Count of ``-v`` flags from the CLI. 0 -> WARNING,
            1 -> INFO, 2+ -> DEBUG.
        log_level: Explicit level name (e.g. ``"DEBUG"``). When provided it
            overrides ``verbosity``.

    Returns:
        The configured package logger.
    """
    if log_level is not None:
        level = logging.getLevelNamesMapping()[log_level.upper()]
    else:
        level = _VERBOSITY_LEVELS.get(verbosity, logging.DEBUG)

    logger = logging.getLogger(PACKAGE_LOGGER)
    logger.setLevel(level)

    # Idempotent: drop any prior handler so repeat calls (e.g. tests) don't
    # stack duplicate output.
    logger.handlers.clear()

    handler = logging.StreamHandler()
    if level <= logging.DEBUG:
        # DEBUG runs want timestamps + source logger to trace per-record detail.
        fmt = "%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    else:
        fmt = "%(levelname)-7s %(message)s"
    handler.setFormatter(logging.Formatter(fmt, datefmt="%H:%M:%S"))
    logger.addHandler(handler)

    # Don't also bubble up to the root logger's default handler.
    logger.propagate = False
    return logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger for ``name`` (pass ``__name__`` from the calling module).

    Modules under the ``mtbl_valuations`` package inherit the level and handler
    set by :func:`configure_logging`.
    """
    return logging.getLogger(name)
