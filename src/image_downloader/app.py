"""Application bootstrap for the local input and session interface."""

from __future__ import annotations

import logging
import sys

from PySide6.QtWidgets import QApplication

from image_downloader.logging_config import configure_logging
from image_downloader.ui.main_window import MainWindow


def create_application() -> QApplication:
    """Create or reuse a QApplication instance."""
    app = QApplication.instance()
    if app is None:
        app = QApplication(sys.argv)
    return app


def run() -> int:
    """Run the desktop input, queue, and manual provider-session window."""
    configure_logging()
    logger = logging.getLogger(__name__)
    app = create_application()
    window = MainWindow()
    window.show()
    logger.info("Image Downloader started in local preparation mode.")
    return app.exec()
