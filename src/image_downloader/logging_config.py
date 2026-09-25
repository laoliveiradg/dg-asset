"""Runtime logging configuration for the foundation stage."""

from __future__ import annotations

import logging


def configure_logging() -> None:
    """Configure basic logging without exposing sensitive runtime data."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler()],
    )
