"""Functional desktop interface for preparing a local image queue."""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from image_downloader.browser.browser_dialog import PROVIDER_NAMES
from image_downloader.browser.controller import SessionController
from image_downloader.browser.session_manager import SessionManager
from image_downloader.browser.session_models import SessionState, SessionStatus
from image_downloader.providers.models import ProviderId
from image_downloader.ui.controllers.input_controller import InputController
from image_downloader.ui.models.queue_table_model import QueueTableModel
from image_downloader.ui.widgets.drop_zone import DropZone


class MainWindow(QMainWindow):
    """Collect local PPTX and pasted URLs, then display the unified queue."""

    def __init__(self, *, session_manager: SessionManager | None = None) -> None:
        super().__init__()
        self.controller = InputController(self)
        self.session_controller = SessionController(self, session_manager=session_manager)
        self.queue_model = QueueTableModel(self)
        self.setWindowTitle("Image Downloader")
        self.setMinimumSize(780, 680)
        self.resize(1180, 820)
        self._build_ui()
        self._connect_signals()
        self._apply_style()
        for provider in self._provider_order():
            self._update_session_status(self.session_controller.session_manager.status(provider))

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralWidget")
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(24, 20, 24, 18)
        root_layout.setSpacing(16)

        header = QHBoxLayout()
        title_group = QVBoxLayout()
        title = QLabel("Image Downloader")
        title.setObjectName("pageTitle")
        subtitle = QLabel("Adicione apresentações ou cole links para preparar os arquivos.")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        title_group.addWidget(title)
        title_group.addWidget(subtitle)
        header.addLayout(title_group, 1)

        self.clear_button = QPushButton("Limpar")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_DialogResetButton))
        self.clear_button.setToolTip("Limpar as entradas e iniciar um novo lote")
        self.clear_button.setAccessibleName("Limpar lote")
        header.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignTop)
        root_layout.addLayout(header)
        root_layout.addWidget(self._build_access_panel())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("contentSplitter")
        self._splitter.setChildrenCollapsible(False)
        self._splitter.addWidget(self._build_input_panel())
        self._splitter.addWidget(self._build_queue_panel())
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 6)
        self._splitter.setSizes([430, 650])
        root_layout.addWidget(self._splitter, 1)

        self.warning_label = QLabel()
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        root_layout.addWidget(self.warning_label)

        self.status_label = QLabel("Pronto para receber apresentações ou links.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAccessibleName("Status da análise")
        root_layout.addWidget(self.status_label)

        self.setCentralWidget(central)

    def _build_access_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("accessPanel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(16, 12, 16, 14)
        layout.setSpacing(8)

        heading = QLabel("Acessos")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        grid = QGridLayout()
        grid.setHorizontalSpacing(12)
        grid.setVerticalSpacing(6)
        self.access_status_labels: dict[ProviderId, QLabel] = {}
        self.access_open_buttons: dict[ProviderId, QPushButton] = {}
        self.access_clear_buttons: dict[ProviderId, QPushButton] = {}

        for row_index, provider in enumerate(self._provider_order()):
            provider_label = QLabel(PROVIDER_NAMES[provider])
            provider_label.setMinimumWidth(150)
            status_label = QLabel("Não iniciada")
            status_label.setObjectName("sessionStatus")
            open_button = QPushButton("Abrir")
            open_button.setObjectName("accessOpenButton")
            open_button.setToolTip(f"Abrir o acesso a {PROVIDER_NAMES[provider]} nesta aplicação")
            open_button.setAccessibleName(f"Abrir acesso a {PROVIDER_NAMES[provider]}")
            clear_button = QPushButton("Limpar acesso")
            clear_button.setObjectName("accessClearButton")
            clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            clear_button.setToolTip(f"Limpar somente a sessão local de {PROVIDER_NAMES[provider]}")
            clear_button.setAccessibleName(f"Limpar acesso a {PROVIDER_NAMES[provider]}")

            open_button.clicked.connect(
                lambda checked=False, selected_provider=provider: self._open_provider(
                    selected_provider
                )
            )
            clear_button.clicked.connect(
                lambda checked=False, selected_provider=provider: self._confirm_clear_provider(
                    selected_provider
                )
            )
            grid.addWidget(provider_label, row_index, 0)
            grid.addWidget(status_label, row_index, 1)
            grid.addWidget(open_button, row_index, 2)
            grid.addWidget(clear_button, row_index, 3)
            self.access_status_labels[provider] = status_label
            self.access_open_buttons[provider] = open_button
            self.access_clear_buttons[provider] = clear_button

        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)
        return panel

    def _build_input_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)

        heading = QLabel("Entradas")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        self.drop_zone = DropZone(panel)
        layout.addWidget(self.drop_zone)

        links_heading = QLabel("Links")
        links_heading.setObjectName("fieldTitle")
        layout.addWidget(links_heading)

        self.links_edit = QTextEdit(panel)
        self.links_edit.setObjectName("linksEditor")
        self.links_edit.setPlaceholderText("Cole um ou vários links aqui...")
        self.links_edit.setAccessibleName("Links para analisar")
        self.links_edit.setMinimumHeight(170)
        self.links_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        layout.addWidget(self.links_edit, 1)
        return panel

    def _build_queue_panel(self) -> QWidget:
        panel = QFrame()
        panel.setObjectName("panel")
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(20, 18, 20, 20)
        layout.setSpacing(14)

        heading = QLabel("Fila preparada")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        self.summary_values: dict[str, QLabel] = {}
        summary_layout = QGridLayout()
        summary_layout.setHorizontalSpacing(18)
        summary_layout.setVerticalSpacing(12)
        summary_specs = (
            ("total", "URLs únicas"),
            ("assetway", "Assetway"),
            ("shutterstock", "Shutterstock"),
            ("envato", "Envato"),
            ("blocked", "Não suportado"),
        )
        for index, (key, caption) in enumerate(summary_specs):
            cell = QWidget(panel)
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(0, 0, 0, 0)
            cell_layout.setSpacing(2)
            value = QLabel("0")
            value.setObjectName("summaryValue")
            label = QLabel(caption)
            label.setObjectName("summaryCaption")
            label.setWordWrap(True)
            cell_layout.addWidget(value)
            cell_layout.addWidget(label)
            summary_layout.addWidget(cell, index // 3, index % 3)
            self.summary_values[key] = value
        layout.addLayout(summary_layout)

        self.queue_table = QTableView(panel)
        self.queue_table.setObjectName("queueTable")
        self.queue_table.setModel(self.queue_model)
        self.queue_table.setAlternatingRowColors(True)
        self.queue_table.setSelectionBehavior(QTableView.SelectionBehavior.SelectRows)
        self.queue_table.setSelectionMode(QTableView.SelectionMode.SingleSelection)
        self.queue_table.setShowGrid(False)
        self.queue_table.verticalHeader().setVisible(False)
        self.queue_table.verticalHeader().setDefaultSectionSize(46)
        self.queue_table.horizontalHeader().setStretchLastSection(True)
        self.queue_table.horizontalHeader().setMinimumSectionSize(78)
        self.queue_table.setColumnWidth(0, 126)
        self.queue_table.setColumnWidth(1, 126)
        self.queue_table.setColumnWidth(2, 116)
        self.queue_table.setAccessibleName("Itens da fila")
        layout.addWidget(self.queue_table, 1)

        self.empty_label = QLabel("Nenhuma URL na fila.")
        self.empty_label.setObjectName("emptyLabel")
        layout.addWidget(self.empty_label)
        return panel

    def _connect_signals(self) -> None:
        self.clear_button.clicked.connect(self._clear_batch)
        self.drop_zone.files_selected_requested.connect(self._select_files)
        self.drop_zone.files_requested.connect(self.controller.add_files)
        self.drop_zone.invalid_paths.connect(self.controller.reject_files)
        self.links_edit.textChanged.connect(
            lambda: self.controller.schedule_text_analysis(self.links_edit.toPlainText())
        )
        self.controller.queue_changed.connect(self._apply_snapshot)
        self.controller.status_changed.connect(self.status_label.setText)
        self.controller.warning_changed.connect(self._show_warnings)
        self.controller.analysis_finished.connect(self._show_analysis_time)
        self.session_controller.status_changed.connect(self._update_session_status)
        self.session_controller.clear_finished.connect(self._on_session_clear_finished)

    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar apresentações",
            "",
            "Apresentações PowerPoint (*.pptx)",
        )
        if paths:
            self.controller.add_files(paths)

    def _clear_batch(self) -> None:
        self.links_edit.blockSignals(True)
        self.links_edit.clear()
        self.links_edit.blockSignals(False)
        self.controller.clear_batch()

    def _apply_snapshot(self, snapshot) -> None:
        self.queue_model.set_items(snapshot.items)
        summary = snapshot.summary
        self.summary_values["total"].setText(str(summary.total))
        self.summary_values["assetway"].setText(
            str(summary.provider_counts[ProviderId.ASSETWAY])
        )
        self.summary_values["shutterstock"].setText(
            str(summary.provider_counts[ProviderId.SHUTTERSTOCK])
        )
        self.summary_values["envato"].setText(str(summary.provider_counts[ProviderId.ENVATO]))
        self.summary_values["blocked"].setText(str(summary.blocked))
        self.empty_label.setVisible(summary.total == 0)

    def _show_warnings(self, message: str) -> None:
        self.warning_label.setText(message)
        self.warning_label.setVisible(bool(message))

    def _show_analysis_time(self, result) -> None:
        if result.snapshot is None:
            return
        count = result.snapshot.summary.total
        elapsed = result.ui_elapsed_ms
        if count:
            self.status_label.setText(f"{count} URLs únicas encontradas em {elapsed:.0f} ms.")
        else:
            self.status_label.setText(f"Nenhuma URL encontrada em {elapsed:.0f} ms.")

    def _open_provider(self, provider: ProviderId) -> None:
        try:
            self.session_controller.open_provider(provider)
        except Exception:
            self.status_label.setText(
                f"Não foi possível abrir o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _confirm_clear_provider(self, provider: ProviderId) -> None:
        answer = QMessageBox.question(
            self,
            "Limpar acesso",
            f"Limpar somente a sessão local de {PROVIDER_NAMES[provider]}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self.access_open_buttons[provider].setEnabled(False)
        self.access_clear_buttons[provider].setEnabled(False)
        try:
            self.session_controller.clear_provider(provider)
        except Exception:
            self.access_open_buttons[provider].setEnabled(True)
            self.access_clear_buttons[provider].setEnabled(True)
            self.status_label.setText(
                f"Não foi possível limpar o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _on_session_clear_finished(self, provider: ProviderId, success: bool) -> None:
        self.access_open_buttons[provider].setEnabled(True)
        self.access_clear_buttons[provider].setEnabled(True)
        if success:
            status = self.session_controller.status(provider)
            if status.reason == "previous_profile_cleanup_pending_restart":
                self.status_label.setText(
                    f"Acesso novo de {PROVIDER_NAMES[provider]} ativo; "
                    "a pasta anterior será removida após reiniciar."
                )
            else:
                self.status_label.setText(f"Acesso local de {PROVIDER_NAMES[provider]} limpo.")
        else:
            self.status_label.setText(
                f"Não foi possível limpar o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _update_session_status(self, status: SessionStatus) -> None:
        labels = {
            SessionState.UNINITIALIZED: "Não iniciada",
            SessionState.UNVERIFIED: "Não verificada",
            SessionState.AUTHENTICATED: "Conectada",
            SessionState.LOGIN_REQUIRED: "Login necessário",
            SessionState.CHECKING: "Limpando acesso...",
            SessionState.ERROR: "Erro",
        }
        label = self.access_status_labels.get(status.provider)
        if label is None:
            return
        label.setText(labels[status.state])
        reason_text = {
            "profile_not_opened": "O perfil será criado ao abrir o acesso.",
            "login_not_verified": "O login não pode ser confirmado automaticamente.",
            "retired_profile_cleanup_pending": "Removendo os dados da sessão anterior.",
            "previous_profile_cleanup_pending_restart": (
                "O profile anterior está bloqueado pelo Chromium; a remoção será repetida "
                "após reiniciar a aplicação."
            ),
        }.get(status.reason, status.reason.replace("_", " "))
        label.setToolTip(reason_text)

    @staticmethod
    def _provider_order() -> tuple[ProviderId, ...]:
        return (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO)

    def resizeEvent(self, event) -> None:
        if hasattr(self, "_splitter"):
            orientation = (
                Qt.Orientation.Vertical if self.width() < 1040 else Qt.Orientation.Horizontal
            )
            if self._splitter.orientation() != orientation:
                self._splitter.setOrientation(orientation)
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        self.session_controller.shutdown()
        super().closeEvent(event)

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QMainWindow, QWidget#centralWidget { background: #edf2f0; }
            QWidget {
                color: #1d3138;
                font-family: 'Aptos', 'Segoe UI', sans-serif;
                font-size: 13px;
            }
            QLabel#pageTitle { color: #19343a; font-size: 27px; font-weight: 700; }
            QLabel#pageSubtitle { color: #65777a; font-size: 14px; }
            QLabel#sectionTitle { color: #19343a; font-size: 17px; font-weight: 650; }
            QLabel#fieldTitle { color: #435a5e; font-size: 12px; font-weight: 650; }
            QFrame#panel { background: #ffffff; border: 1px solid #d9e3df; border-radius: 9px; }
            QFrame#accessPanel {
                background: #ffffff;
                border: 1px solid #d9e3df;
                border-radius: 8px;
            }
            QFrame#dropZone { background: #f5f8f6; border: 1px dashed #a9bbb5; border-radius: 8px; }
            QLabel#dropTitle { color: #234149; font-size: 15px; font-weight: 650; }
            QLabel#dropHint { color: #718386; }
            QPushButton {
                min-height: 38px;
                padding: 0 14px;
                border-radius: 6px;
                font-weight: 600;
            }
            QPushButton#selectFilesButton {
                color: #ffffff;
                background: #247d76;
                border: 1px solid #247d76;
            }
            QPushButton#selectFilesButton:hover { background: #1d6a65; }
            QPushButton#clearButton {
                color: #344b50;
                background: #ffffff;
                border: 1px solid #cbd8d4;
            }
            QPushButton#clearButton:hover { background: #f4f7f5; }
            QPushButton#accessOpenButton {
                color: #ffffff;
                background: #247d76;
                border: 1px solid #247d76;
                min-height: 32px;
            }
            QPushButton#accessClearButton {
                color: #4f6264;
                background: #ffffff;
                border: 1px solid #d5dfdb;
                min-height: 32px;
            }
            QLabel#sessionStatus { color: #617578; }
            QTextEdit#linksEditor {
                background: #fbfcfb;
                border: 1px solid #d6e0dc;
                border-radius: 6px;
                padding: 10px;
                selection-background-color: #81c9bc;
            }
            QTextEdit#linksEditor:focus { border: 1px solid #428f86; }
            QLabel#summaryValue { color: #23766e; font-size: 20px; font-weight: 700; }
            QLabel#summaryCaption { color: #6a7d80; font-size: 11px; }
            QTableView#queueTable {
                background: #ffffff;
                alternate-background-color: #f6f9f7;
                border: 1px solid #e0e8e4;
                border-radius: 6px;
                selection-background-color: #e4f2ee;
                selection-color: #18373b;
            }
            QHeaderView::section {
                color: #63777a;
                background: #f5f8f6;
                border: none;
                border-bottom: 1px solid #dfe7e3;
                padding: 9px 8px;
                font-size: 11px;
                font-weight: 650;
            }
            QLabel#emptyLabel { color: #7b8b8d; padding: 2px; }
            QLabel#statusLabel { color: #3d5d60; font-size: 12px; }
            QLabel#warningLabel {
                color: #8f4e38;
                background: #fff3eb;
                border: 1px solid #f0d2c1;
                border-radius: 6px;
                padding: 9px 12px;
            }
            QSplitter::handle { background: #dce6e2; }
            """
        )
