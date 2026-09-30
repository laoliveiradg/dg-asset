"""Runtime logging configuration for the foundation stage."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler

from image_downloader.runtime_paths import default_runtime_root


def configure_logging() -> None:
    """Configure basic logging without exposing sensitive runtime data."""

    log_directory = default_runtime_root() / "logs"
    log_directory.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            RotatingFileHandler(
                log_directory / "asset.log",
                maxBytes=2_000_000,
                backupCount=2,
                encoding="utf-8",
            ),
            logging.StreamHandler(),
        ],
    )
