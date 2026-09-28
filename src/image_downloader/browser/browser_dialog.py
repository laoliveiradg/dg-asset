"""LEGACY INACTIVE: QtWebEngine provider dialog retained for rollback only."""

from __future__ import annotations

from time import perf_counter

from PySide6.QtCore import Qt, QUrl, Signal
from PySide6.QtWebEngineCore import QWebEnginePage, QWebEngineProfile
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtWidgets import QDialog, QHBoxLayout, QPushButton, QVBoxLayout

from image_downloader.providers.models import ProviderId

PROVIDER_START_URLS = {
    ProviderId.ASSETWAY: "https://plataformaa.assetway.com.br/",
    ProviderId.SHUTTERSTOCK: "https://www.shutterstock.com/",
    ProviderId.ENVATO: "https://elements.envato.com/",
}

PROVIDER_NAMES = {
    ProviderId.ASSETWAY: "Assetway",
    ProviderId.SHUTTERSTOCK: "Shutterstock",
    ProviderId.ENVATO: "Envato Elements",
}


class BrowserDialog(QDialog):
    """Legacy QtWebEngine view; the active UI uses managed Google Chrome instead."""

    presented = Signal(object, float)

    def __init__(
        self,
        provider: ProviderId,
        profile: QWebEngineProfile,
        parent=None,
        *,
        navigate: bool = True,
    ) -> None:
        super().__init__(parent)
        self.provider = provider
        self.initial_url = PROVIDER_START_URLS[provider]
        self._created_at = perf_counter()
        self._presented = False
        self.setWindowTitle(f"Acesso a {PROVIDER_NAMES[provider]}")
        self.resize(1040, 760)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.web_view = QWebEngineView(self)
        self.web_page = QWebEnginePage(profile, self.web_view)
        self.web_view.setPage(self.web_page)

        close_button = QPushButton("Fechar", self)
        close_button.setAccessibleName(f"Fechar acesso a {PROVIDER_NAMES[provider]}")
        close_button.clicked.connect(self.close)

        footer = QHBoxLayout()
        footer.addStretch()
        footer.addWidget(close_button)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)
        layout.addWidget(self.web_view, 1)
        layout.addLayout(footer)

        if navigate:
            self.navigate_to_start_page()

    def navigate_to_start_page(self) -> None:
        self.web_view.setUrl(QUrl(self.initial_url))

    def showEvent(self, event) -> None:
        super().showEvent(event)
        if not self._presented:
            self._presented = True
            self.presented.emit(self.provider, (perf_counter() - self._created_at) * 1000)

    def closeEvent(self, event) -> None:
        self.web_view.stop()
        super().closeEvent(event)