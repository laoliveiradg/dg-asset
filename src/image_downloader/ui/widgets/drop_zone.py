"""Accessible PPTX drag-and-drop area with extension validation."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QDragEnterEvent, QDropEvent
from PySide6.QtWidgets import QFrame, QLabel, QPushButton, QStyle, QVBoxLayout


class DropZone(QFrame):
    """Accept one or more local PPTX files and report rejected paths separately."""

    files_requested = Signal(list)
    invalid_paths = Signal(list)
    files_selected_requested = Signal()

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.setObjectName("dropZone")
        self.setAcceptDrops(True)
        self.setMinimumHeight(154)
        self.setAccessibleName("Área para adicionar apresentações PPTX")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 15, 14, 15)
        layout.setSpacing(7)

        title = QLabel("Arraste apresentações aqui")
        title.setObjectName("dropTitle")
        title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        hint = QLabel("ou selecione um ou vários arquivos .pptx")
        hint.setObjectName("dropHint")
        hint.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.select_files_button = QPushButton("Selecionar arquivos")
        self.select_files_button.setObjectName("selectFilesButton")
        self.select_files_button.setIcon(
            self.style().standardIcon(QStyle.StandardPixmap.SP_DialogOpenButton)
        )
        self.select_files_button.setToolTip("Selecionar apresentações PowerPoint")
        self.select_files_button.setAccessibleName("Selecionar arquivos PPTX")
        self.select_files_button.clicked.connect(self.files_selected_requested.emit)

        layout.addWidget(title)
        layout.addWidget(hint)
        layout.addWidget(self.select_files_button, 0, Qt.AlignmentFlag.AlignCenter)

    @staticmethod
    def partition_paths(paths: list[str]) -> tuple[list[str], list[str]]:
        accepted: list[str] = []
        rejected: list[str] = []
        for raw_path in paths:
            if Path(raw_path).suffix.lower() == ".pptx":
                accepted.append(raw_path)
            else:
                rejected.append(raw_path)
        return accepted, rejected

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        accepted, _ = self.partition_paths(paths)
        if accepted:
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event: QDropEvent) -> None:
        paths = [url.toLocalFile() for url in event.mimeData().urls() if url.isLocalFile()]
        accepted, rejected = self.partition_paths(paths)
        if accepted:
            self.files_requested.emit(accepted)
            if rejected:
                self.invalid_paths.emit(rejected)
            event.acceptProposedAction()
        else:
            if rejected:
                self.invalid_paths.emit(rejected)
            event.ignore()