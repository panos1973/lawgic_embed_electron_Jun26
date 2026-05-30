"""logsetup.py — durable, rotating file logging for the pipeline.

All pipeline modules log under the "lawgic" logger tree (e.g. "lawgic.embed",
"lawgic.orchestrator"). init() attaches a rotating file handler once so a run
leaves an auditable record on disk — written next to the state DB, which in the
packaged app lives in the Electron userData dir. This is what lets you inspect
what happened in the background (especially embedding) after a run finishes.

Console/stdout is deliberately left untouched: the Electron shell reads stdout
as JSON-line events, so logs go to the file only (plus the structured progress
events the orchestrator already emits).
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

import config

ROOT = "lawgic"
_initialized = False


def log_path() -> str:
    """Log file path: <dir of STATE_DB>/lawgic.log (same dir the app already writes)."""
    base = os.path.dirname(os.path.abspath(config.STATE_DB)) or "."
    return os.path.join(base, "lawgic.log")


def init(level: int = logging.INFO) -> str:
    """Attach the rotating file handler to the 'lawgic' logger (idempotent).

    Returns the log file path so callers can surface it to the user.
    """
    global _initialized
    path = log_path()
    if _initialized:
        return path
    try:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=2_000_000, backupCount=5,
                                      encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger = logging.getLogger(ROOT)
        logger.setLevel(level)
        # avoid duplicate handlers if init() is somehow called twice
        if not any(isinstance(h, RotatingFileHandler) for h in logger.handlers):
            logger.addHandler(handler)
        logger.propagate = False          # don't leak to python root / stdout
        _initialized = True
        logger.info("=== logging initialized -> %s ===", path)
    except Exception:
        # logging must never break the pipeline; carry on without a file handler
        pass
    return path


def get(name: str) -> logging.Logger:
    """Get a child logger under the 'lawgic' tree (e.g. get('embed'))."""
    return logging.getLogger(f"{ROOT}.{name}")
