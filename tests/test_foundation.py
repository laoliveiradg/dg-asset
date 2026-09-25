import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

from image_downloader import __version__
from image_downloader.ui.main_window import MainWindow


def test_package_import() -> None:
    assert __version__ == "0.1.0"


def test_basic_modules_import() -> None:
    import image_downloader.__main__ as __main__
    import image_downloader.app as app_module
    from image_downloader.logging_config import configure_logging

    assert callable(__main__.run)
    assert callable(app_module.run)
    assert callable(configure_logging)


def test_main_window_starts() -> None:
    app = QApplication.instance() or QApplication([])
    window = MainWindow()

    assert app is not None
    assert window.windowTitle() == "Image Downloader"
    assert window.centralWidget() is not None
