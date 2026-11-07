"""Structured JSON logging on stdout (one event per line)."""
from __future__ import annotations

import logging
import sys

from pythonjsonlogger.json import JsonFormatter

_CONFIGURED = False


def configure_logging(level: str = "INFO") -> None:
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(
        JsonFormatter(
            "%(levelname)s %(name)s %(message)s",
            rename_fields={"levelname": "level", "name": "logger"},
            timestamp="ts",
        )
    )
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    logging.getLogger("httpx").setLevel(logging.WARNING)
    _CONFIGURED = True
