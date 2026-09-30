"""Functional desktop interface for preparing a local image queue."""

from __future__ import annotations

import os
from dataclasses import dataclass
from importlib import resources
from pathlib import Path

from PySide6.QtCore import QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSplitter,
    QStyle,
    QTableView,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from image_downloader.archive.service import ArchiveResult, BatchArchiveService
from image_downloader.chrome.config import PROVIDER_NAMES, PROVIDER_START_URLS
from image_downloader.chrome.controller import ChromeSessionController
from image_downloader.chrome.models import (
    ChromeRuntimeError,
    ChromeSessionState,
    ChromeSessionStatus,
)
from image_downloader.chrome.runtime import ChromeRuntime
from image_downloader.downloads.assisted import AssistedDownloadMonitor
from image_downloader.downloads.models import DownloadResult, DownloadStatus
from image_downloader.providers.assetway.downloader import AssetwayDownloader
from image_downloader.providers.assetway.errors import AssetwayDownloadError
from image_downloader.providers.capabilities import ProviderExecutionMode
from image_downloader.providers.execution_policy import (
    DEFAULT_PROVIDER_EXECUTION_POLICY,
    ProviderExecutionPolicy,
)
from image_downloader.providers.interactive import (
    InteractiveProviderError,
    open_interactive_provider,
)
from image_downloader.providers.models import ProviderId
from image_downloader.queue.models import QueueError, QueueItem, QueueState
from image_downloader.ui.controllers.input_controller import InputController
from image_downloader.ui.models.queue_table_model import QueueTableModel
from image_downloader.ui.theme import APP_STYLESHEET
from image_downloader.ui.widgets.drop_zone import DropZone
from image_downloader.ui.workers.archive_worker import ArchiveWorker
from image_downloader.ui.workers.assetway_download_worker import AssetwayDownloadWorker
from image_downloader.ui.workers.assisted_download_worker import AssistedDownloadWorker


@dataclass(slots=True)
class BatchUiResult:
    total: int
    completed: int
    failed: int
    unsupported: int
    zip_count: int = 0
    zip_created: bool = False
    exported_path: Path | None = None


class MainWindow(QMainWindow):
    """Collect local PPTX and pasted URLs, then display the unified queue."""

    def __init__(
        self,
        *,
        chrome_runtime: ChromeRuntime | None = None,
        execution_policy: ProviderExecutionPolicy = DEFAULT_PROVIDER_EXECUTION_POLICY,
    ) -> None:
        super().__init__()
        self.execution_policy = execution_policy
        self.controller = InputController(self)
        self.session_controller = ChromeSessionController(
            self,
            chrome_runtime=chrome_runtime,
            execution_policy=execution_policy,
        )
        self._shutdown_complete = False
        self._active_download_item_id: str | None = None
        self._assetway_download_worker: AssetwayDownloadWorker | None = None
        self._diagnostic_active = False
        self._assetway_session_busy = False
        self._batch_item_ids: list[str] = []
        self._batch_total = 0
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files: list[Path] = []
        self._active_batch_item_ids: list[str] = []
        self._last_batch_failed_item_ids: list[str] = []
        self._batch_result: BatchUiResult | None = None
        self._archive_worker: ArchiveWorker | None = None
        self._assisted_download_worker: AssistedDownloadWorker | None = None
        self._download_generation = 0
        self.queue_model = QueueTableModel(self)
        self.setWindowTitle("Asset")
        self.setMinimumSize(720, 620)
        self.resize(1280, 760)
        self._build_ui()
        self._connect_signals()
        self._apply_style()
        for provider in self._provider_order():
            if self._uses_managed_chrome(provider):
                self._update_session_status(self.session_controller.status(provider))

    def _build_ui(self) -> None:
        central = QWidget(self)
        central.setObjectName("centralWidget")
        root_layout = QVBoxLayout(central)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        header_frame = QFrame(central)
        header_frame.setObjectName("appHeader")
        header = QHBoxLayout(header_frame)
        header.setContentsMargins(28, 16, 28, 16)
        header.setSpacing(12)
        self.brand_logo = QLabel(header_frame)
        self.brand_logo.setObjectName("brandLogo")
        self.brand_logo.setFixedSize(44, 44)
        self.brand_logo.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.brand_logo.setAccessibleName("Logo Asset")
        logo_data = (
            resources.files("image_downloader.ui")
            .joinpath("assets", "asset-logo.png")
            .read_bytes()
        )
        logo_pixmap = QPixmap()
        if not logo_pixmap.loadFromData(logo_data, "PNG"):
            raise RuntimeError("The Asset logo could not be loaded.")
        self.setWindowIcon(QIcon(logo_pixmap))
        self.brand_logo.setPixmap(
            logo_pixmap.scaled(
                QSize(44, 44),
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        header.addWidget(self.brand_logo)
        title_group = QVBoxLayout()
        title_group.setSpacing(1)
        self.brand_title = QLabel("Asset")
        self.brand_title.setObjectName("pageTitle")
        subtitle = QLabel("Download e organização de ativos")
        subtitle.setObjectName("pageSubtitle")
        subtitle.setWordWrap(True)
        title_group.addWidget(self.brand_title)
        title_group.addWidget(subtitle)
        header.addLayout(title_group, 1)

        self.clear_button = QPushButton("Limpar")
        self.clear_button.setObjectName("clearButton")
        self.clear_button.setToolTip("Limpar as entradas e iniciar um novo lote")
        self.clear_button.setAccessibleName("Limpar lote")
        self.settings_button = QPushButton("Acessos")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setToolTip("Gerenciar login e acessos dos provedores")
        self.settings_button.clicked.connect(self._show_access_settings)
        header.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignTop)
        root_layout.addWidget(header_frame)

        self.access_dialog = QDialog(self)
        self.access_dialog.setWindowTitle("Configurações · Acessos")
        self.access_dialog.setMinimumWidth(680)
        access_dialog_layout = QVBoxLayout(self.access_dialog)
        access_dialog_layout.addWidget(self._build_access_panel())

        self._splitter = QSplitter(Qt.Orientation.Horizontal)
        self._splitter.setObjectName("contentSplitter")
        self._splitter.setChildrenCollapsible(False)
        self.input_panel = self._build_input_panel()
        self.queue_panel = self._build_queue_panel()
        self._splitter.addWidget(self.input_panel)
        self._splitter.addWidget(self.queue_panel)
        self._splitter.setStretchFactor(0, 4)
        self._splitter.setStretchFactor(1, 6)
        self._splitter.setSizes([450, 750])

        self.content_viewport = QWidget(central)
        self.content_viewport.setObjectName("contentViewport")
        content_layout = QVBoxLayout(self.content_viewport)
        content_layout.setContentsMargins(24, 22, 24, 18)
        content_layout.setSpacing(12)
        content_layout.addWidget(self._splitter, 1)

        self.warning_label = QLabel()
        self.warning_label.setObjectName("warningLabel")
        self.warning_label.setWordWrap(True)
        self.warning_label.setVisible(False)
        content_layout.addWidget(self.warning_label)

        self.content_scroll = QScrollArea(central)
        self.content_scroll.setObjectName("contentScroll")
        self.content_scroll.setWidgetResizable(True)
        self.content_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.content_scroll.setWidget(self.content_viewport)
        root_layout.addWidget(self.content_scroll, 1)

        self.status_label = QLabel("Pronto para receber apresentações ou links.")
        self.status_label.setObjectName("statusLabel")
        self.status_label.setAccessibleName("Status da análise")
        status_frame = QFrame(central)
        status_frame.setObjectName("statusBar")
        status_layout = QHBoxLayout(status_frame)
        status_layout.setContentsMargins(28, 8, 28, 8)
        status_layout.addWidget(self.status_label)
        root_layout.addWidget(status_frame)

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
            mode = self.execution_policy.mode_for(provider)
            provider_label = QLabel(PROVIDER_NAMES[provider])
            provider_label.setMinimumWidth(150)
            status_label = QLabel(self._execution_status_text(mode))
            status_label.setObjectName("sessionStatus")
            open_button = QPushButton(
                "Abrir no navegador"
                if mode == ProviderExecutionMode.INTERACTIVE_REQUIRED
                else "Abrir"
            )
            open_button.setObjectName("accessOpenButton")
            open_button.setToolTip(f"Abrir o acesso a {PROVIDER_NAMES[provider]} nesta aplicação")
            open_button.setAccessibleName(f"Abrir acesso a {PROVIDER_NAMES[provider]}")
            clear_button = QPushButton("Limpar acesso")
            clear_button.setObjectName("accessClearButton")
            clear_button.setIcon(self.style().standardIcon(QStyle.StandardPixmap.SP_TrashIcon))
            clear_button.setToolTip(f"Limpar somente a sessão local de {PROVIDER_NAMES[provider]}")
            clear_button.setAccessibleName(f"Limpar acesso a {PROVIDER_NAMES[provider]}")
            clear_button.setEnabled(
                mode in {ProviderExecutionMode.AUTOMATED, ProviderExecutionMode.UNVALIDATED}
            )
            open_button.setEnabled(
                mode
                in {
                    ProviderExecutionMode.INTERACTIVE_REQUIRED,
                    ProviderExecutionMode.AUTOMATED,
                    ProviderExecutionMode.UNVALIDATED,
                }
            )

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
        layout.setSpacing(12)

        heading = QLabel("Adicionar arquivos e links")
        heading.setObjectName("sectionTitle")
        layout.addWidget(heading)

        hint = QLabel("Use apresentações PPTX, links individuais ou ambos.")
        hint.setObjectName("sectionHint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

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
        layout.setSpacing(12)

        self.queue_heading = QLabel("Imagens encontradas")
        self.queue_heading.setObjectName("sectionTitle")
        layout.addWidget(self.queue_heading)

        self.summary_values: dict[str, QLabel] = {}
        self.provider_summary_widget = QWidget(panel)
        summary_layout = QGridLayout(self.provider_summary_widget)
        summary_layout.setContentsMargins(0, 0, 0, 0)
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
            cell = QFrame(panel)
            cell.setObjectName("statCard")
            cell_layout = QVBoxLayout(cell)
            cell_layout.setContentsMargins(12, 9, 12, 9)
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
        layout.addWidget(self.provider_summary_widget)

        self.batch_summary_label = QLabel("Nenhuma imagem pronta para processamento.")
        self.batch_summary_label.setObjectName("batchSummary")
        self.batch_summary_label.setWordWrap(True)
        layout.addWidget(self.batch_summary_label)

        self.problems_button = QPushButton("VER PROBLEMAS", panel)
        self.problems_button.setObjectName("problemsButton")
        self.problems_button.setVisible(False)
        self.problems_button.clicked.connect(self._show_batch_problems)
        layout.addWidget(self.problems_button, 0, Qt.AlignmentFlag.AlignLeft)

        self.problems_dialog = QDialog(self)
        self.problems_dialog.setWindowTitle("Problemas do processamento")
        self.problems_dialog.setMinimumWidth(520)
        problems_layout = QVBoxLayout(self.problems_dialog)
        self.problems_text_label = QLabel()
        self.problems_text_label.setWordWrap(True)
        self.problems_text_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        problems_layout.addWidget(self.problems_text_label)
        close_problems_button = QPushButton("Fechar")
        close_problems_button.clicked.connect(self.problems_dialog.close)
        problems_layout.addWidget(close_problems_button, 0, Qt.AlignmentFlag.AlignRight)

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
        self.queue_table.setVisible(False)
        self.details_button = QPushButton("Ver detalhes", panel)
        self.details_button.setObjectName("detailsButton")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._toggle_details)
        layout.addWidget(self.details_button, 0, Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.queue_table)

        action_heading = QLabel("Downloads")
        action_heading.setObjectName("fieldTitle")
        layout.addWidget(action_heading)
        self.download_selection_hint = QLabel(
            "Adicione imagens para identificar automaticamente o que pode ser processado."
        )
        self.download_selection_hint.setObjectName("actionHint")
        self.download_selection_hint.setWordWrap(True)
        layout.addWidget(self.download_selection_hint)

        self.open_item_button = QPushButton("Abrir no navegador", panel)
        self.open_item_button.setObjectName("openItemButton")
        self.open_item_button.setEnabled(False)
        self.open_item_button.setVisible(False)
        self.open_item_button.clicked.connect(self._open_selected_queue_item)
        self.download_item_button = QPushButton("BAIXAR IMAGENS", panel)
        self.download_item_button.setObjectName("downloadItemButton")
        self.download_item_button.setToolTip(
            "Processar automaticamente as imagens compatíveis desta etapa"
        )
        self.download_item_button.setEnabled(False)
        self.download_item_button.clicked.connect(self._download_selected_assetway_item)
        self.diagnose_item_button = QPushButton("Diagnosticar controles", panel)
        self.diagnose_item_button.setObjectName("diagnoseItemButton")
        self.diagnose_item_button.setToolTip(
            "Listar metadados sanitizados dos controles sem iniciar download"
        )
        self.diagnose_item_button.setEnabled(False)
        self.diagnose_item_button.setVisible(
            os.environ.get("IMAGE_DOWNLOADER_DEV_TOOLS") == "1"
        )
        self.diagnose_item_button.clicked.connect(self._diagnose_selected_assetway_item)
        self.retry_download_button = QPushButton("Tentar novamente", panel)
        self.retry_download_button.setObjectName("retryDownloadButton")
        self.retry_download_button.setEnabled(False)
        self.retry_download_button.setVisible(False)
        self.retry_download_button.clicked.connect(self._retry_selected_assetway_item)
        actions = QHBoxLayout()
        actions.addStretch(1)
        actions.addWidget(self.open_item_button)
        actions.addWidget(self.diagnose_item_button)
        actions.addWidget(self.retry_download_button)
        layout.addLayout(actions)
        layout.addWidget(self.download_item_button)

        self.progress_heading = QLabel("Progresso")
        self.progress_heading.setObjectName("fieldTitle")
        self.progress_heading.setVisible(False)
        layout.addWidget(self.progress_heading)
        self.download_progress_label = QLabel("Aguardando um item Assetway selecionado.")
        self.download_progress_label.setObjectName("downloadProgress")
        self.download_progress_label.setWordWrap(True)
        self.download_progress_label.setVisible(False)
        layout.addWidget(self.download_progress_label)
        self.batch_progress_bar = QProgressBar(panel)
        self.batch_progress_bar.setObjectName("batchProgressBar")
        self.batch_progress_bar.setTextVisible(True)
        self.batch_progress_bar.setVisible(False)
        layout.addWidget(self.batch_progress_bar)
        self.assetway_login_button = QPushButton("ENTRAR NO ASSETWAY", panel)
        self.assetway_login_button.setObjectName("assetwayLoginButton")
        self.assetway_login_button.setVisible(False)
        self.assetway_login_button.clicked.connect(self._open_assetway_login)
        layout.addWidget(self.assetway_login_button)

        self.empty_label = QLabel("Nenhuma URL na fila.")
        self.empty_label.setObjectName("emptyLabel")
        layout.addWidget(self.empty_label)
        layout.addStretch(1)
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
        self.session_controller.shutdown_finished.connect(self._finish_shutdown)
        self.queue_table.selectionModel().selectionChanged.connect(
            lambda *_: self._update_selected_item_action()
        )

    def _select_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self,
            "Selecionar apresentações",
            "",
            "Apresentações PowerPoint (*.pptx)",
        )
        if paths:
            self.controller.add_files(paths)

    def _show_access_settings(self) -> None:
        self.access_dialog.show()
        self.access_dialog.raise_()
        self.access_dialog.activateWindow()

    def _toggle_details(self, visible: bool) -> None:
        self.queue_table.setVisible(visible)
        self.queue_table.setMinimumHeight(240 if visible else 0)
        self.details_button.setText("Ocultar detalhes" if visible else "Ver detalhes")

    def _clear_batch(self) -> None:
        self.links_edit.blockSignals(True)
        self.links_edit.clear()
        self.links_edit.blockSignals(False)
        self._batch_item_ids.clear()
        self._batch_total = 0
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files.clear()
        self._active_batch_item_ids.clear()
        self._last_batch_failed_item_ids.clear()
        self._batch_result = None
        self._download_generation += 1
        self.assetway_login_button.setVisible(False)
        self.problems_button.setVisible(False)
        self.problems_dialog.hide()
        self.batch_progress_bar.setVisible(False)
        self.provider_summary_widget.setVisible(True)
        self.queue_heading.setText("Imagens encontradas")
        self._set_batch_visual_state("empty")
        self.progress_heading.setVisible(False)
        self.download_progress_label.setText("Aguardando imagens para processar.")
        self.download_progress_label.setVisible(False)
        self.status_label.setText("Pronto.")
        self.controller.clear_batch()

    def _apply_snapshot(self, snapshot) -> None:
        selected_item = self._selected_queue_item()
        selected_item_id = selected_item.item_id if selected_item is not None else None
        self.queue_model.set_items(snapshot.items)
        if selected_item_id is not None:
            selected_row = next(
                (
                    row
                    for row, item in enumerate(self.queue_model.items)
                    if item.item_id == selected_item_id
                ),
                None,
            )
            if selected_row is not None:
                self.queue_table.selectRow(selected_row)
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
        if self._batch_result is not None and self._processable_ready_items():
            self._batch_result = None
            self._last_batch_failed_item_ids.clear()
        if self._batch_total:
            self._render_batch_progress()
        elif self._batch_result is not None:
            self._render_batch_result()
        else:
            self._render_preprocessing_summary(snapshot.items, summary.total, summary.blocked)
        self._update_selected_item_action()
        self.empty_label.setVisible(summary.total == 0)

    def _render_preprocessing_summary(
        self,
        items: tuple[QueueItem, ...],
        total: int,
        unsupported: int,
    ) -> None:
        automatic = sum(
            self.execution_policy.mode_for(item.provider) == ProviderExecutionMode.AUTOMATED
            and item.state == QueueState.READY
            for item in items
        )
        interactive = sum(
            self.execution_policy.mode_for(item.provider)
            == ProviderExecutionMode.INTERACTIVE_REQUIRED
            and item.state == QueueState.READY
            for item in items
        )
        self.queue_heading.setText("Imagens encontradas")
        self.provider_summary_widget.setVisible(True)
        self._set_batch_visual_state("ready" if automatic + interactive else "empty")
        self.batch_summary_label.setText(
            f"{total} imagens encontradas\n\n"
            f"{automatic} automáticas · {interactive} exigirão interação · "
            f"{unsupported} não suportadas"
        )
        self.problems_button.setVisible(False)
        self.batch_progress_bar.setVisible(False)
        self.progress_heading.setVisible(False)
        self.download_progress_label.setVisible(False)

    def _render_batch_progress(self) -> None:
        self.queue_heading.setText("Baixando imagens")
        self.provider_summary_widget.setVisible(False)
        self._set_batch_visual_state("processing")
        self.batch_summary_label.setText(
            f"Baixando imagens\n\n{self._batch_finished} de {self._batch_total} concluídas"
        )
        self.problems_button.setVisible(False)
        self.batch_progress_bar.setRange(0, self._batch_total)
        self.batch_progress_bar.setValue(self._batch_finished)
        self.batch_progress_bar.setFormat("%v de %m")
        self.batch_progress_bar.setVisible(True)
        self.progress_heading.setVisible(True)
        self.download_progress_label.setVisible(True)

    def _render_batch_result(self) -> None:
        result = self._batch_result
        if result is None:
            return
        lines = [
            "Processamento concluído",
            "",
            f"{result.completed} baixadas · {result.failed} não baixadas · "
            f"{result.unsupported} não suportadas",
        ]
        if result.zip_created:
            if result.exported_path is not None:
                lines.append(f"ZIP salvo em {result.exported_path.parent.name}")
            else:
                lines.append(f"ZIP criado com {result.zip_count} imagens")
        self.queue_heading.setText("Resultado do lote")
        self.provider_summary_widget.setVisible(False)
        self._set_batch_visual_state("partial" if result.failed else "success")
        self.batch_summary_label.setText("\n".join(lines))
        self.batch_progress_bar.setVisible(False)
        self.progress_heading.setVisible(False)
        self.download_progress_label.setVisible(False)
        self.problems_button.setText(f"VER {result.failed} PROBLEMAS")
        self.problems_button.setVisible(result.failed > 0)

    def _set_batch_visual_state(self, state: str) -> None:
        self.batch_summary_label.setProperty("uiState", state)
        self.batch_summary_label.style().unpolish(self.batch_summary_label)
        self.batch_summary_label.style().polish(self.batch_summary_label)

    def _show_batch_problems(self) -> None:
        items = []
        for item_id in self._last_batch_failed_item_ids:
            item = self.controller.queue_manager.get_item(item_id)
            if item.error is None:
                continue
            reference = item.asset_reference or "Sem referência"
            items.append(
                f"{PROVIDER_NAMES[item.provider]} · {reference}\n"
                f"{self._friendly_failure_message(item.error)}"
            )
        self.problems_text_label.setText("\n\n".join(items) or "Nenhum problema registrado.")
        self.problems_dialog.show()
        self.problems_dialog.raise_()
        self.problems_dialog.activateWindow()

    @staticmethod
    def _friendly_failure_message(error: QueueError) -> str:
        messages = {
            "download_action_unverified": "Não foi possível localizar a opção de download.",
            "authentication_required": "É necessário entrar novamente.",
            "ambiguous_download": (
                "Mais de um arquivo foi detectado e não foi possível identificar o correto."
            ),
            "quality_unverified": "Não foi possível confirmar a qualidade original.",
            "interactive_open_failed": "Não foi possível abrir o item no navegador.",
            "assisted_download_timeout": "O download não foi recebido no tempo esperado.",
            "assisted_download_invalid": "O arquivo recebido não é um download final válido.",
            "assisted_download_format_unverified": (
                "Não foi possível confirmar o formato do arquivo recebido."
            ),
        }
        return messages.get(error.code, error.safe_message)

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
        mode = self.execution_policy.mode_for(provider)
        if mode == ProviderExecutionMode.INTERACTIVE_REQUIRED:
            try:
                opened = open_interactive_provider(
                    provider,
                    PROVIDER_START_URLS[provider],
                    execution_policy=self.execution_policy,
                )
            except InteractiveProviderError:
                opened = False
            if not opened:
                self.status_label.setText(
                    f"Não foi possível abrir o acesso a {PROVIDER_NAMES[provider]}."
                )
            return
        if not self._uses_managed_chrome(provider):
            self.status_label.setText(f"Acesso indisponível para {PROVIDER_NAMES[provider]}.")
            return
        try:
            self.session_controller.open_provider(provider)
        except ChromeRuntimeError:
            self.status_label.setText(
                f"Não foi possível abrir o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _confirm_clear_provider(self, provider: ProviderId) -> None:
        if not self._uses_managed_chrome(provider):
            return
        answer = QMessageBox.question(
            self,
            "Limpar acesso",
            f"Limpar somente a sessão local de {PROVIDER_NAMES[provider]}?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        try:
            submitted = self.session_controller.clear_provider(provider)
            if not submitted:
                self.access_open_buttons[provider].setEnabled(True)
                self.access_clear_buttons[provider].setEnabled(True)
        except ChromeRuntimeError:
            self.access_open_buttons[provider].setEnabled(True)
            self.access_clear_buttons[provider].setEnabled(True)
            self.status_label.setText(
                f"Não foi possível limpar o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _on_session_clear_finished(self, provider: ProviderId, success: bool) -> None:
        self.access_open_buttons[provider].setEnabled(True)
        self.access_clear_buttons[provider].setEnabled(True)
        if success:
            self.status_label.setText(f"Acesso local de {PROVIDER_NAMES[provider]} limpo.")
        else:
            self.status_label.setText(
                f"Não foi possível limpar o acesso a {PROVIDER_NAMES[provider]}."
            )

    def _update_session_status(self, status: ChromeSessionStatus) -> None:
        if not self._uses_managed_chrome(status.provider):
            return
        if status.provider == ProviderId.ASSETWAY:
            self._assetway_session_busy = status.state == ChromeSessionState.CHECKING
        if status.state == ChromeSessionState.ERROR:
            if status.reason == "chrome_not_found":
                label_text = "Google Chrome não encontrado"
            else:
                label_text = "Erro ao iniciar" if status.reason == "chrome_start_failed" else "Erro"
        elif status.state == ChromeSessionState.CHECKING:
            label_text = (
                "Iniciando Chrome..."
                if status.reason == "chrome_starting"
                else "Limpando profile..."
            )
        elif status.chrome_running:
            label_text = "Automação pronta" if self.execution_policy.mode_for(
                status.provider
            ) == ProviderExecutionMode.AUTOMATED else "Chrome aberto · Sessão não verificada"
        else:
            label_text = "Automação validada" if self.execution_policy.mode_for(
                status.provider
            ) == ProviderExecutionMode.AUTOMATED else "Chrome fechado · Sessão não verificada"
        label = self.access_status_labels.get(status.provider)
        if label is None:
            return
        is_busy = status.state == ChromeSessionState.CHECKING
        self.access_open_buttons[status.provider].setEnabled(not is_busy)
        self.access_clear_buttons[status.provider].setEnabled(not is_busy)
        label.setText(label_text)
        label.setToolTip(
            "Instale o Google Chrome para usar o download automático."
            if status.reason == "chrome_not_found"
            else "A autenticação permanece não verificada até uma validação específica do provider."
        )
        self._update_selected_item_action()

    def _update_selected_item_action(self) -> None:
        item = self._selected_queue_item()
        eligible = self._processable_ready_items()
        busy = (
            self._active_download_item_id is not None
            or bool(self._batch_item_ids)
            or self._assetway_session_busy
            or self._archive_worker is not None
        )
        self.open_item_button.setEnabled(
            item is not None
            and item.state == QueueState.READY
            and self.execution_policy.mode_for(item.provider)
            == ProviderExecutionMode.INTERACTIVE_REQUIRED
        )
        self.download_item_button.setEnabled(
            not busy and bool(eligible)
        )
        self.diagnose_item_button.setEnabled(
            not busy
            and item is not None
            and item.state == QueueState.READY
            and item.provider == ProviderId.ASSETWAY
        )
        self.retry_download_button.setEnabled(
            not busy
            and item is not None
            and item.provider == ProviderId.ASSETWAY
            and item.state == QueueState.FAILED
            and item.error is not None
            and item.error.retryable
        )
        if self.download_item_button.isEnabled():
            self.download_item_button.setText(f"BAIXAR {len(eligible)} IMAGENS")
            self.download_item_button.setToolTip(
                "Processar todas as imagens suportadas deste lote"
            )
            self.download_selection_hint.setText(
                f"{len(eligible)} itens prontos para processamento."
            )
        elif busy:
            self.download_item_button.setText("PROCESSANDO...")
            self.download_item_button.setToolTip("O lote está em processamento")
            self.download_selection_hint.setText(
                "Processamento em andamento. Nenhuma seleção é necessária."
            )
        else:
            self.download_item_button.setText("BAIXAR IMAGENS")
            self.download_item_button.setToolTip(
                "Não há novas imagens suportadas prontas para processamento"
            )
            self.download_selection_hint.setText(
                "Nenhuma nova imagem está pronta para processamento."
            )

    def _download_selected_assetway_item(self) -> None:
        if self._active_download_item_id is not None or self._batch_item_ids:
            return
        eligible = self._processable_ready_items()
        if not eligible:
            return
        self._batch_item_ids = [item.item_id for item in eligible]
        self._active_batch_item_ids = list(self._batch_item_ids)
        self._last_batch_failed_item_ids.clear()
        self._batch_result = None
        self._download_generation += 1
        self._batch_total = len(self._batch_item_ids)
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files.clear()
        self.assetway_login_button.setVisible(False)
        self.clear_button.setEnabled(False)
        self.status_label.setText("Processando...")
        self.download_progress_label.setText("Preparando downloads...")
        self._render_batch_progress()
        self._start_next_batch_item()

    def _open_assetway_login(self) -> None:
        self.assetway_login_button.setVisible(False)
        self._open_provider(ProviderId.ASSETWAY)
        self.download_progress_label.setText(
            "Entre no Assetway na janela aberta. Depois, clique em Baixar imagens novamente."
        )

    def _retry_selected_assetway_item(self) -> None:
        item = self._selected_queue_item()
        if (
            item is None
            or item.provider != ProviderId.ASSETWAY
            or item.state != QueueState.FAILED
            or item.error is None
            or not item.error.retryable
        ):
            return
        ready_item = self.controller.queue_manager.prepare_retry(item.item_id)
        self.controller.publish_queue_snapshot()
        self._submit_assetway_download(ready_item)

    def _processable_ready_items(self) -> list[QueueItem]:
        return [
            item
            for item in self.controller.queue_manager.list_items()
            if item.provider
            in {ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO}
            and item.state == QueueState.READY
        ]

    def _start_next_batch_item(self) -> None:
        while self._batch_item_ids:
            item_id = self._batch_item_ids.pop(0)
            item = self.controller.queue_manager.get_item(item_id)
            if item.state == QueueState.READY:
                if item.provider == ProviderId.ASSETWAY:
                    self._submit_assetway_download(item)
                    return
                if item.provider in {ProviderId.SHUTTERSTOCK, ProviderId.ENVATO}:
                    self._submit_interactive_download(item)
                    return
            self._batch_finished += 1
        self._finish_download_batch()

    def _submit_interactive_download(self, item: QueueItem) -> None:
        runtime = self.session_controller.ensure_runtime()
        monitor = AssistedDownloadMonitor(
            Path.home() / "Downloads",
            runtime.process_manager.profile_factory.runtime_root,
        )
        before = monitor.snapshot()
        try:
            opened = open_interactive_provider(
                item.provider,
                item.normalized_url,
                execution_policy=self.execution_policy,
            )
        except InteractiveProviderError:
            opened = False
        if not opened:
            self.controller.queue_manager.start_processing(item.item_id)
            self.controller.queue_manager.mark_failed(
                item.item_id,
                QueueError(
                    "interactive_open_failed",
                    f"Não foi possível abrir o item {PROVIDER_NAMES[item.provider]}.",
                    item.provider,
                    True,
                ),
            )
            self._batch_finished += 1
            self._batch_failed += 1
            self._render_batch_progress()
            self._start_next_batch_item()
            return
        self._active_download_item_id = item.item_id
        waiting_message = f"Aguardando o download no {PROVIDER_NAMES[item.provider]}..."
        self.status_label.setText("Processando...")
        self.download_progress_label.setText(waiting_message)
        worker = AssistedDownloadWorker(
            monitor,
            self.controller.queue_manager,
            item.item_id,
            before,
        )
        generation = self._download_generation
        worker.signals.completed.connect(
            lambda outcome, current=generation: self._on_assetway_download_completed(
                outcome, current
            )
        )
        self._assisted_download_worker = worker
        if not self.session_controller.submit_worker(worker):
            self._assisted_download_worker = None
            self._active_download_item_id = None
            self._batch_finished += 1
            self._batch_failed += 1
            self._render_batch_progress()
            self._start_next_batch_item()

    def _finish_download_batch(self) -> None:
        total = self._batch_total
        completed = self._batch_completed
        failed = self._batch_failed
        files = list(self._batch_files)
        unsupported = self.controller.queue_manager.summary().blocked
        self._last_batch_failed_item_ids = [
            item_id
            for item_id in self._active_batch_item_ids
            if self.controller.queue_manager.get_item(item_id).state == QueueState.FAILED
        ]
        self._batch_result = BatchUiResult(total, completed, failed, unsupported)
        self._batch_item_ids.clear()
        self._active_batch_item_ids.clear()
        self._batch_total = 0
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files.clear()
        if files:
            self._render_batch_result()
            self._create_batch_archive(files, completed, failed, total)
            return
        self.clear_button.setEnabled(True)
        self.status_label.setText(f"{total} imagens processadas.")
        self._render_batch_result()
        self._update_selected_item_action()

    def _create_batch_archive(
        self,
        files: list[Path],
        completed: int,
        failed: int,
        total: int,
    ) -> None:
        self.download_progress_label.setText("Criando ZIP...")
        self.download_progress_label.setVisible(True)
        runtime = self.session_controller.ensure_runtime()
        service = BatchArchiveService(runtime.process_manager.profile_factory.runtime_root)
        worker = ArchiveWorker(service, self.controller.batch_id, files)
        worker.signals.completed.connect(
            lambda outcome: self._on_archive_completed(
                service,
                outcome,
                completed,
                failed,
                total,
            )
        )
        self._archive_worker = worker
        if not self.session_controller.submit_worker(worker):
            self._archive_worker = None
            self.clear_button.setEnabled(True)
            self.download_progress_label.setText("Não foi possível iniciar a criação do ZIP.")

    def _on_archive_completed(
        self,
        service: BatchArchiveService,
        outcome: object,
        completed: int,
        failed: int,
        total: int,
    ) -> None:
        self._archive_worker = None
        self.clear_button.setEnabled(True)
        if not isinstance(outcome, ArchiveResult):
            self.download_progress_label.setVisible(False)
            self.status_label.setText(f"{total} imagens processadas.")
            self._render_batch_result()
            self._update_selected_item_action()
            return
        if self._batch_result is not None:
            self._batch_result.zip_created = True
            self._batch_result.zip_count = outcome.file_count
        destination = QFileDialog.getExistingDirectory(
            self,
            "Escolher pasta para salvar o ZIP",
            "",
        )
        if not destination:
            self.status_label.setText(f"{total} imagens processadas.")
            self._render_batch_result()
            self._update_selected_item_action()
            return
        try:
            exported = service.export(outcome.temporary_path, Path(destination))
        except OSError:
            self.status_label.setText(f"{total} imagens processadas.")
            self._render_batch_result()
        else:
            service.cleanup_batch(self.controller.batch_id)
            if self._batch_result is not None:
                self._batch_result.exported_path = exported
            self.status_label.setText(f"{total} imagens processadas.")
            self._render_batch_result()
        self._update_selected_item_action()

    def _diagnose_selected_assetway_item(self) -> None:
        item = self._selected_queue_item()
        if item is None or item.provider != ProviderId.ASSETWAY or item.state != QueueState.READY:
            return
        if self._active_download_item_id is not None or self._assetway_session_busy:
            return
        self._active_download_item_id = item.item_id
        self._diagnostic_active = True
        self.clear_button.setEnabled(False)
        runtime = self.session_controller.ensure_runtime()
        worker = AssetwayDownloadWorker(
            AssetwayDownloader(runtime),
            self.controller.queue_manager,
            item.item_id,
            diagnostic_only=True,
        )
        generation = self._download_generation
        worker.signals.progress.connect(
            lambda message, current=generation: self._on_assetway_download_progress(
                message, current
            )
        )
        worker.signals.completed.connect(
            lambda outcome, current=generation: self._on_assetway_download_completed(
                outcome, current
            )
        )
        self._assetway_download_worker = worker
        if not self.session_controller.submit_worker(worker):
            self._active_download_item_id = None
            self._assetway_download_worker = None
            self._diagnostic_active = False
            self.clear_button.setEnabled(True)
        self._update_selected_item_action()

    def _submit_assetway_download(self, item: QueueItem) -> None:
        if self._active_download_item_id is not None or self._assetway_session_busy:
            return
        self._active_download_item_id = item.item_id
        self._diagnostic_active = False
        self.clear_button.setEnabled(False)
        self.access_open_buttons[ProviderId.ASSETWAY].setEnabled(False)
        self.access_clear_buttons[ProviderId.ASSETWAY].setEnabled(False)
        runtime = self.session_controller.ensure_runtime()
        worker = AssetwayDownloadWorker(
            AssetwayDownloader(runtime),
            self.controller.queue_manager,
            item.item_id,
        )
        generation = self._download_generation
        worker.signals.progress.connect(
            lambda message, current=generation: self._on_assetway_download_progress(
                message, current
            )
        )
        worker.signals.completed.connect(
            lambda outcome, current=generation: self._on_assetway_download_completed(
                outcome, current
            )
        )
        self._assetway_download_worker = worker
        if not self.session_controller.submit_worker(worker):
            self._active_download_item_id = None
            self._assetway_download_worker = None
            self._batch_finished += 1
            self._batch_failed += 1
            self._render_batch_progress()
            self._start_next_batch_item()
            return
        self._update_selected_item_action()

    def _on_assetway_download_progress(
        self,
        message: str,
        generation: int | None = None,
    ) -> None:
        if generation is not None and generation != self._download_generation:
            return
        if message == "Preparando ativo...":
            self.controller.publish_queue_snapshot()
        if self._batch_total:
            position = self._batch_finished + 1
            if message == "Validando...":
                message = f"Validando {position} de {self._batch_total}..."
            elif message in {"Preparando ativo...", "Abrindo ativo..."}:
                message = "Preparando downloads..."
            else:
                message = f"Baixando {position} de {self._batch_total}..."
        self.status_label.setText("Processando..." if self._batch_total else message)
        self.download_progress_label.setText(message)

    def _on_assetway_download_completed(
        self,
        outcome: object,
        generation: int | None = None,
    ) -> None:
        if generation is not None and generation != self._download_generation:
            return
        diagnostic_active = self._diagnostic_active
        self._diagnostic_active = False
        self._active_download_item_id = None
        self._assetway_download_worker = None
        self._assisted_download_worker = None
        if not self._batch_total:
            self.clear_button.setEnabled(True)
        status = self.session_controller.status(ProviderId.ASSETWAY)
        self._update_session_status(status)
        self.controller.publish_queue_snapshot()
        if diagnostic_active and isinstance(outcome, int):
            message = (
                f"Diagnóstico concluído: {outcome} candidatos sanitizados registrados; "
                "nenhum controle foi acionado."
            )
            self.status_label.setText(message)
            self.download_progress_label.setText(message)
        elif self._batch_total and self._outcome_error_code(outcome) == "authentication_required":
            self._batch_finished += 1
            self._batch_failed += 1
            self._batch_item_ids.clear()
            self._batch_total = 0
            self._batch_finished = 0
            self._batch_completed = 0
            self._batch_failed = 0
            self.clear_button.setEnabled(True)
            self.assetway_login_button.setVisible(True)
            message = "É necessário entrar novamente no Assetway."
            self.status_label.setText(message)
            self.download_progress_label.setText(message)
            self._update_selected_item_action()
            return
        elif self._batch_total:
            self._batch_finished += 1
            if isinstance(outcome, DownloadResult) and outcome.status == DownloadStatus.COMPLETED:
                self._batch_completed += 1
                if outcome.file_path is not None:
                    self._batch_files.append(outcome.file_path)
            else:
                self._batch_failed += 1
            self._render_batch_progress()
            self.status_label.setText("Processando...")
            if isinstance(outcome, DownloadResult) and outcome.status == DownloadStatus.COMPLETED:
                if outcome.provider in {ProviderId.SHUTTERSTOCK, ProviderId.ENVATO}:
                    self.download_progress_label.setText("Download recebido. Continuando...")
            self._start_next_batch_item()
            return
        elif isinstance(outcome, AssetwayDownloadError):
            self.status_label.setText(outcome.safe_message)
            self.download_progress_label.setText(outcome.safe_message)
        elif isinstance(outcome, DownloadResult) and outcome.status == DownloadStatus.COMPLETED:
            message = (
                f"Concluído: {outcome.file_name} · {outcome.source_format} · "
                f"{outcome.bytes_received} bytes · {outcome.quality_label} · "
                f"{outcome.timings.total_ms:.0f} ms"
            )
            self.status_label.setText(message)
            self.download_progress_label.setText(message)
        elif isinstance(outcome, DownloadResult) and outcome.error is not None:
            self.status_label.setText(outcome.error.safe_message)
            self.download_progress_label.setText(outcome.error.safe_message)
        else:
            self.status_label.setText("O download Assetway falhou com segurança.")
            self.download_progress_label.setText("O download Assetway falhou com segurança.")
        self._update_selected_item_action()

    @staticmethod
    def _outcome_error_code(outcome: object) -> str | None:
        if isinstance(outcome, AssetwayDownloadError):
            return outcome.code
        if isinstance(outcome, DownloadResult) and outcome.error is not None:
            return outcome.error.code
        return None

    def _open_selected_queue_item(self) -> None:
        item = self._selected_queue_item()
        if (
            item is None
            or item.state != QueueState.READY
            or self.execution_policy.mode_for(item.provider)
            != ProviderExecutionMode.INTERACTIVE_REQUIRED
        ):
            return
        try:
            opened = open_interactive_provider(
                item.provider,
                item.normalized_url,
                execution_policy=self.execution_policy,
            )
        except InteractiveProviderError:
            opened = False
        if opened:
            self.status_label.setText("Item aberto no navegador padrão.")
        else:
            self.status_label.setText("Não foi possível abrir o item no navegador padrão.")

    def _selected_queue_item(self) -> QueueItem | None:
        selected_rows = self.queue_table.selectionModel().selectedRows()
        if not selected_rows:
            return None
        row = selected_rows[0].row()
        if not 0 <= row < len(self.queue_model.items):
            return None
        return self.queue_model.items[row]

    @staticmethod
    def _execution_status_text(mode: ProviderExecutionMode) -> str:
        if mode == ProviderExecutionMode.INTERACTIVE_REQUIRED:
            return "Navegador padrão · Login manual"
        if mode == ProviderExecutionMode.AUTOMATED:
            return "Automação validada"
        if mode == ProviderExecutionMode.UNAVAILABLE:
            return "Indisponível"
        return "Chrome fechado · Sessão não verificada"

    def _uses_managed_chrome(self, provider: ProviderId) -> bool:
        return self.execution_policy.mode_for(provider) in {
            ProviderExecutionMode.AUTOMATED,
            ProviderExecutionMode.UNVALIDATED,
        }

    @staticmethod
    def _provider_order() -> tuple[ProviderId, ...]:
        return (ProviderId.ASSETWAY, ProviderId.SHUTTERSTOCK, ProviderId.ENVATO)

    def resizeEvent(self, event) -> None:
        if hasattr(self, "_splitter"):
            orientation = (
                Qt.Orientation.Vertical if self.width() < 940 else Qt.Orientation.Horizontal
            )
            if self._splitter.orientation() != orientation:
                self._splitter.setOrientation(orientation)
            compact = orientation == Qt.Orientation.Vertical
            self.input_panel.setMinimumHeight(410 if compact else 0)
            self.queue_panel.setMinimumHeight(500 if compact else 0)
            self._splitter.setMinimumHeight(930 if compact else 0)
        super().resizeEvent(event)

    def closeEvent(self, event) -> None:
        if self._shutdown_complete:
            super().closeEvent(event)
            return
        event.ignore()
        self.setEnabled(False)
        self.status_label.setText("Encerrando o Chrome gerenciado...")
        self.session_controller.shutdown()

    def _finish_shutdown(self) -> None:
        self._shutdown_complete = True
        self.close()

    def _apply_style(self) -> None:
        self.setStyleSheet(APP_STYLESHEET)
