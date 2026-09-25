"""Qt model presenting QueueItems without exposing their full URLs."""

from __future__ import annotations

from PySide6.QtCore import QAbstractTableModel, QModelIndex, Qt
from PySide6.QtGui import QColor

from image_downloader.input.models import SourceType
from image_downloader.providers.models import ProviderId
from image_downloader.queue.models import QueueItem, QueueState


class QueueTableModel(QAbstractTableModel):
    """Read-only table model backed directly by QueueManager snapshots."""

    HEADERS = ("Status", "Provider", "Referência", "Origem")
    PROVIDER_LABELS = {
        ProviderId.ASSETWAY: "Assetway",
        ProviderId.SHUTTERSTOCK: "Shutterstock",
        ProviderId.ENVATO: "Envato",
        ProviderId.UNKNOWN: "Não suportado",
    }
    STATE_LABELS = {
        QueueState.PENDING: "Pendente",
        QueueState.READY: "Pronto",
        QueueState.PROCESSING: "Em análise",
        QueueState.COMPLETED: "Concluído",
        QueueState.FAILED: "Falhou",
        QueueState.BLOCKED: "Não suportado",
    }

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self._items: tuple[QueueItem, ...] = ()

    def rowCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self._items)

    def columnCount(self, parent: QModelIndex = QModelIndex()) -> int:
        return 0 if parent.isValid() else len(self.HEADERS)

    def headerData(
        self,
        section: int,
        orientation: Qt.Orientation,
        role: int = Qt.ItemDataRole.DisplayRole,
    ) -> str | None:
        if role == Qt.ItemDataRole.DisplayRole and orientation == Qt.Orientation.Horizontal:
            if 0 <= section < len(self.HEADERS):
                return self.HEADERS[section]
        return None

    def data(self, index: QModelIndex, role: int = Qt.ItemDataRole.DisplayRole):
        if not index.isValid() or not (0 <= index.row() < len(self._items)):
            return None

        item = self._items[index.row()]
        if role == Qt.ItemDataRole.DisplayRole:
            return self._display_value(item, index.column())
        if role == Qt.ItemDataRole.ToolTipRole:
            return self._origin_tooltip(item)
        if role == Qt.ItemDataRole.ForegroundRole and index.column() == 0:
            if item.state == QueueState.BLOCKED:
                return QColor("#a54f36")
            if item.state == QueueState.READY:
                return QColor("#24766e")
        if role == Qt.ItemDataRole.TextAlignmentRole and index.column() == 2:
            return int(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        return None

    def set_items(self, items: tuple[QueueItem, ...]) -> None:
        self.beginResetModel()
        self._items = tuple(items)
        self.endResetModel()

    @property
    def items(self) -> tuple[QueueItem, ...]:
        return self._items

    def _display_value(self, item: QueueItem, column: int) -> str:
        if column == 0:
            return self.STATE_LABELS[item.state]
        if column == 1:
            return self.PROVIDER_LABELS[item.provider]
        if column == 2:
            return item.asset_reference or "—"
        if column == 3:
            if len(item.occurrences) > 1:
                return f"{len(item.occurrences)} origens"
            return self._origin_names(item)[0] if item.occurrences else "Origem desconhecida"
        return ""

    def _origin_names(self, item: QueueItem) -> list[str]:
        names: list[str] = []
        for occurrence in item.occurrences:
            if occurrence.source_type == SourceType.pasted_text:
                name = "Links"
            else:
                name = occurrence.file_name or occurrence.source_name or "Apresentação"
            if name not in names:
                names.append(name)
        return names or ["Origem desconhecida"]

    def _origin_tooltip(self, item: QueueItem) -> str:
        details: list[str] = []
        for occurrence in item.occurrences:
            origin = (
                "Links"
                if occurrence.source_type == SourceType.pasted_text
                else occurrence.file_name or occurrence.source_name or "Apresentação"
            )
            if occurrence.slide_number is not None:
                origin = f"{origin}, slide {occurrence.slide_number}"
            if origin not in details:
                details.append(origin)
        return "\n".join(details)