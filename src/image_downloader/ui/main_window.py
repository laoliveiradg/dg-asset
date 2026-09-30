"""Functional desktop interface for preparing a local image queue."""

from __future__ import annotations

import os
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
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
from image_downloader.ui.widgets.drop_zone import DropZone
from image_downloader.ui.workers.archive_worker import ArchiveWorker
from image_downloader.ui.workers.assetway_download_worker import AssetwayDownloadWorker
from image_downloader.ui.workers.assisted_download_worker import AssistedDownloadWorker


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
        self._archive_worker: ArchiveWorker | None = None
        self._assisted_download_worker: AssistedDownloadWorker | None = None
        self._download_generation = 0
        self.queue_model = QueueTableModel(self)
        self.setWindowTitle("Image Downloader")
        self.setMinimumSize(780, 680)
        self.resize(1180, 820)
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
        self.settings_button = QPushButton("Acessos")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.setToolTip("Gerenciar login e acessos dos provedores")
        self.settings_button.clicked.connect(self._show_access_settings)
        header.addWidget(self.settings_button, 0, Qt.AlignmentFlag.AlignTop)
        header.addWidget(self.clear_button, 0, Qt.AlignmentFlag.AlignTop)
        root_layout.addLayout(header)

        self.access_dialog = QDialog(self)
        self.access_dialog.setWindowTitle("Configurações · Acessos")
        self.access_dialog.setMinimumWidth(680)
        access_dialog_layout = QVBoxLayout(self.access_dialog)
        access_dialog_layout.addWidget(self._build_access_panel())

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
        layout.setSpacing(14)

        heading = QLabel("Adicionar arquivos e links")
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

        heading = QLabel("Imagens encontradas")
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

        self.batch_summary_label = QLabel("Nenhuma imagem pronta para processamento.")
        self.batch_summary_label.setObjectName("batchSummary")
        self.batch_summary_label.setWordWrap(True)
        layout.addWidget(self.batch_summary_label)

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
        layout.addWidget(self.queue_table, 1)

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

        progress_heading = QLabel("Progresso")
        progress_heading.setObjectName("fieldTitle")
        layout.addWidget(progress_heading)
        self.download_progress_label = QLabel("Aguardando um item Assetway selecionado.")
        self.download_progress_label.setObjectName("downloadProgress")
        self.download_progress_label.setWordWrap(True)
        layout.addWidget(self.download_progress_label)
        self.assetway_login_button = QPushButton("ENTRAR NO ASSETWAY", panel)
        self.assetway_login_button.setObjectName("assetwayLoginButton")
        self.assetway_login_button.setVisible(False)
        self.assetway_login_button.clicked.connect(self._open_assetway_login)
        layout.addWidget(self.assetway_login_button)

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
        self._download_generation += 1
        self.assetway_login_button.setVisible(False)
        self.download_progress_label.setText("Aguardando imagens para processar.")
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
        self._update_selected_item_action()
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
        automatic = sum(
            self.execution_policy.mode_for(item.provider) == ProviderExecutionMode.AUTOMATED
            and item.state == QueueState.READY
            for item in snapshot.items
        )
        interactive = sum(
            self.execution_policy.mode_for(item.provider)
            == ProviderExecutionMode.INTERACTIVE_REQUIRED
            for item in snapshot.items
        )
        unvalidated = sum(
            self.execution_policy.mode_for(item.provider) == ProviderExecutionMode.UNVALIDATED
            for item in snapshot.items
        )
        unavailable = sum(item.provider == ProviderId.UNKNOWN for item in snapshot.items)
        self.batch_summary_label.setText(
            f"{summary.total} imagens encontradas · {automatic} prontas para download "
            f"automático · {interactive} exigem interação · {unvalidated} ainda não "
            f"validadas · {unavailable} indisponíveis"
        )
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
            "A autenticação permanece não verificada até uma validação específica do provider."
        )
        self._update_selected_item_action()

    def _update_selected_item_action(self) -> None:
        item = self._selected_queue_item()
        eligible = self._processable_ready_items()
        busy = (
            self._active_download_item_id is not None
            or bool(self._batch_item_ids)
            or self._assetway_session_busy
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
                "Processar automaticamente todas as imagens compatíveis desta etapa"
            )
            self.download_selection_hint.setText(
                f"{len(eligible)} itens prontos para download automático."
            )
        elif busy:
            self.download_item_button.setText("PROCESSANDO...")
            self.download_item_button.setToolTip("O processamento automático está em andamento")
            self.download_selection_hint.setText(
                "Processamento automático em andamento. Nenhuma seleção é necessária."
            )
        else:
            self.download_item_button.setText("BAIXAR IMAGENS")
            self.download_item_button.setToolTip(
                "Não há imagens compatíveis prontas para download automático"
            )
            self.download_selection_hint.setText(
                "Nenhuma imagem está pronta para download automático nesta etapa."
            )

    def _download_selected_assetway_item(self) -> None:
        if self._active_download_item_id is not None or self._batch_item_ids:
            return
        eligible = self._processable_ready_items()
        if not eligible:
            return
        self._batch_item_ids = [item.item_id for item in eligible]
        self._download_generation += 1
        self._batch_total = len(self._batch_item_ids)
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files.clear()
        self.assetway_login_button.setVisible(False)
        self.clear_button.setEnabled(False)
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
            self._start_next_batch_item()
            return
        self._active_download_item_id = item.item_id
        waiting_message = f"Aguardando o download no {PROVIDER_NAMES[item.provider]}..."
        self.status_label.setText(waiting_message)
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
            self._start_next_batch_item()

    def _finish_download_batch(self) -> None:
        total = self._batch_total
        completed = self._batch_completed
        failed = self._batch_failed
        files = list(self._batch_files)
        self._batch_item_ids.clear()
        self._batch_total = 0
        self._batch_finished = 0
        self._batch_completed = 0
        self._batch_failed = 0
        self._batch_files.clear()
        if files:
            self._create_batch_archive(files, completed, failed, total)
            return
        self.clear_button.setEnabled(True)
        message = f"Processamento finalizado: {completed} concluídas"
        if failed:
            message += f", {failed} com falha"
        message += f" de {total}."
        self.status_label.setText(message)
        self.download_progress_label.setText(message)
        self._update_selected_item_action()

    def _create_batch_archive(
        self,
        files: list[Path],
        completed: int,
        failed: int,
        total: int,
    ) -> None:
        self.download_progress_label.setText("Criando ZIP...")
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
            self.download_progress_label.setText(
                "Os downloads terminaram, mas não foi possível criar o ZIP."
            )
            self._update_selected_item_action()
            return
        destination = QFileDialog.getExistingDirectory(
            self,
            "Escolher pasta para salvar o ZIP",
            "",
        )
        if not destination:
            self.download_progress_label.setText(
                f"ZIP temporário pronto com {outcome.file_count} imagens. Escolha o destino depois."
            )
            self._update_selected_item_action()
            return
        try:
            exported = service.export(outcome.temporary_path, Path(destination))
        except OSError:
            self.download_progress_label.setText(
                "Não foi possível salvar o ZIP no destino escolhido."
            )
        else:
            service.cleanup_batch(self.controller.batch_id)
            message = f"Processamento finalizado: {completed} concluídas"
            if failed:
                message += f", {failed} com falha"
            message += f" de {total}. ZIP salvo como {exported.name}."
            self.status_label.setText(message)
            self.download_progress_label.setText(message)
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
        self.status_label.setText(message)
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
                if outcome.provider in {ProviderId.SHUTTERSTOCK, ProviderId.ENVATO}:
                    self.status_label.setText("Download recebido. Continuando...")
                    self.download_progress_label.setText("Download recebido. Continuando...")
            else:
                self._batch_failed += 1
            remaining = self._batch_total - self._batch_finished
            if not (
                isinstance(outcome, DownloadResult)
                and outcome.status == DownloadStatus.COMPLETED
                and outcome.provider in {ProviderId.SHUTTERSTOCK, ProviderId.ENVATO}
            ):
                self.download_progress_label.setText(
                    f"{self._batch_total} imagens · {self._batch_completed} concluídas · "
                    f"{self._batch_failed} falharam · {remaining} restantes"
                )
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
                Qt.Orientation.Vertical if self.width() < 1040 else Qt.Orientation.Horizontal
            )
            if self._splitter.orientation() != orientation:
                self._splitter.setOrientation(orientation)
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
            QPushButton#downloadItemButton {
                color: #ffffff;
                background: #176f68;
                border: 2px solid #176f68;
                min-height: 50px;
                font-size: 14px;
                font-weight: 750;
            }
            QPushButton#downloadItemButton:hover {
                background: #125d57;
                border-color: #125d57;
            }
            QPushButton#downloadItemButton:pressed {
                background: #0d4a46;
                border-color: #0d4a46;
            }
            QPushButton#downloadItemButton:disabled {
                color: #899694;
                background: #dce4e1;
                border-color: #d1dbd7;
            }
            QPushButton#openItemButton, QPushButton#retryDownloadButton,
            QPushButton#diagnoseItemButton {
                color: #42595c;
                background: #ffffff;
                border: 1px solid #cbd8d4;
            }
            QLabel#actionHint { color: #667a7d; }
            QLabel#downloadProgress {
                color: #234f52;
                background: #eef7f4;
                border: 1px solid #cfe2dc;
                border-radius: 6px;
                padding: 10px 12px;
                font-weight: 600;
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
            QLabel#batchSummary {
                color: #355c60;
                background: #f3f8f6;
                border: 1px solid #d8e6e1;
                border-radius: 6px;
                padding: 9px 11px;
            }
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
