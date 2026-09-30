"""Centralized visual tokens and QSS for the desktop interface."""

COLORS = {
    "canvas": "#f8fafc",
    "surface": "#ffffff",
    "surface_subtle": "#f1f5f9",
    "border": "#e2e8f0",
    "border_strong": "#cbd5e1",
    "text": "#1e293b",
    "text_muted": "#64748b",
    "text_faint": "#94a3b8",
    "primary": "#2563eb",
    "primary_hover": "#1d4ed8",
    "primary_pressed": "#1e40af",
    "primary_soft": "#eff6ff",
    "success": "#059669",
    "success_soft": "#ecfdf5",
    "warning": "#b45309",
    "warning_soft": "#fffbeb",
    "danger": "#b91c1c",
    "danger_soft": "#fef2f2",
}


def _color(name: str) -> str:
    return COLORS[name]


APP_STYLESHEET = f"""
QMainWindow, QWidget#centralWidget, QWidget#contentViewport {{
    background: {_color("canvas")};
}}
QWidget {{
    color: {_color("text")};
    font-family: 'Segoe UI Variable', 'Segoe UI', sans-serif;
    font-size: 13px;
}}
QToolTip {{
    color: {_color("text")};
    background: {_color("surface")};
    border: 1px solid {_color("border_strong")};
    padding: 6px;
}}
QFrame#appHeader {{
    background: {_color("surface")};
    border: none;
    border-bottom: 1px solid {_color("border")};
}}
QLabel#brandLogo {{ background: transparent; }}
QLabel#pageTitle {{
    color: {_color("text")};
    font-size: 22px;
    font-weight: 750;
}}
QLabel#pageSubtitle {{
    color: {_color("text_muted")};
    font-size: 12px;
}}
QLabel#sectionTitle {{
    color: {_color("text")};
    font-size: 16px;
    font-weight: 700;
}}
QLabel#fieldTitle {{
    color: #475569;
    font-size: 11px;
    font-weight: 700;
}}
QLabel#sectionHint, QLabel#actionHint {{
    color: {_color("text_muted")};
    font-size: 12px;
}}
QFrame#panel, QFrame#accessPanel {{
    background: {_color("surface")};
    border: 1px solid {_color("border")};
    border-radius: 12px;
}}
QFrame#statCard {{
    background: {_color("canvas")};
    border: 1px solid {_color("border")};
    border-radius: 9px;
}}
QFrame#dropZone {{
    background: {_color("canvas")};
    border: 1px dashed {_color("border_strong")};
    border-radius: 12px;
}}
QFrame#dropZone:hover {{
    background: {_color("primary_soft")};
    border-color: #93c5fd;
}}
QLabel#dropIcon {{
    color: {_color("primary")};
    background: #dbeafe;
    border-radius: 9px;
    font-size: 18px;
    font-weight: 800;
}}
QLabel#dropTitle {{
    color: {_color("text")};
    font-size: 14px;
    font-weight: 700;
}}
QLabel#dropHint {{
    color: {_color("text_muted")};
    font-size: 11px;
}}
QPushButton {{
    min-height: 36px;
    padding: 0 13px;
    border: 1px solid transparent;
    border-radius: 8px;
    font-weight: 650;
}}
QPushButton:focus {{
    border-color: #93c5fd;
}}
QPushButton#selectFilesButton, QPushButton#accessOpenButton,
QPushButton#assetwayLoginButton {{
    color: #ffffff;
    background: {_color("primary")};
    border-color: {_color("primary")};
}}
QPushButton#selectFilesButton:hover, QPushButton#accessOpenButton:hover,
QPushButton#assetwayLoginButton:hover {{
    background: {_color("primary_hover")};
    border-color: {_color("primary_hover")};
}}
QPushButton#settingsButton, QPushButton#clearButton,
QPushButton#detailsButton, QPushButton#accessClearButton,
QPushButton#openItemButton, QPushButton#retryDownloadButton,
QPushButton#diagnoseItemButton, QPushButton#problemsButton {{
    color: #475569;
    background: {_color("surface")};
    border-color: {_color("border_strong")};
}}
QPushButton#settingsButton:hover, QPushButton#clearButton:hover,
QPushButton#detailsButton:hover, QPushButton#accessClearButton:hover,
QPushButton#openItemButton:hover, QPushButton#retryDownloadButton:hover,
QPushButton#diagnoseItemButton:hover, QPushButton#problemsButton:hover {{
    color: {_color("primary")};
    background: {_color("primary_soft")};
    border-color: #bfdbfe;
}}
QPushButton#detailsButton:checked {{
    color: {_color("primary")};
    background: {_color("primary_soft")};
    border-color: #93c5fd;
}}
QPushButton#downloadItemButton {{
    color: #ffffff;
    background: {_color("primary")};
    border: 1px solid {_color("primary")};
    min-height: 50px;
    font-size: 14px;
    font-weight: 800;
}}
QPushButton#downloadItemButton:hover {{
    background: {_color("primary_hover")};
    border-color: {_color("primary_hover")};
}}
QPushButton#downloadItemButton:pressed {{
    background: {_color("primary_pressed")};
    border-color: {_color("primary_pressed")};
}}
QPushButton#downloadItemButton:disabled {{
    color: {_color("text_faint")};
    background: {_color("surface_subtle")};
    border-color: {_color("border")};
}}
QPushButton#problemsButton {{
    color: {_color("warning")};
    background: {_color("warning_soft")};
    border-color: #fed7aa;
}}
QTextEdit#linksEditor {{
    color: {_color("text")};
    background: {_color("surface")};
    border: 1px solid {_color("border_strong")};
    border-radius: 9px;
    padding: 10px;
    selection-background-color: #bfdbfe;
}}
QTextEdit#linksEditor:focus {{
    border-color: {_color("primary")};
}}
QLabel#summaryValue {{
    color: {_color("text")};
    font-size: 20px;
    font-weight: 800;
}}
QLabel#summaryCaption {{
    color: {_color("text_muted")};
    font-size: 10px;
    font-weight: 600;
}}
QLabel#batchSummary {{
    color: #475569;
    background: {_color("canvas")};
    border: 1px solid {_color("border")};
    border-left: 4px solid {_color("border_strong")};
    border-radius: 9px;
    padding: 12px 14px;
}}
QLabel#batchSummary[uiState="ready"] {{
    color: #1e40af;
    background: {_color("primary_soft")};
    border-color: #bfdbfe;
    border-left-color: {_color("primary")};
}}
QLabel#batchSummary[uiState="processing"] {{
    color: #1e40af;
    background: {_color("primary_soft")};
    border-color: #bfdbfe;
    border-left-color: {_color("primary")};
}}
QLabel#batchSummary[uiState="success"] {{
    color: #047857;
    background: {_color("success_soft")};
    border-color: #a7f3d0;
    border-left-color: {_color("success")};
}}
QLabel#batchSummary[uiState="partial"] {{
    color: #92400e;
    background: {_color("warning_soft")};
    border-color: #fde68a;
    border-left-color: #f59e0b;
}}
QLabel#downloadProgress {{
    color: #1e40af;
    background: {_color("primary_soft")};
    border: 1px solid #bfdbfe;
    border-radius: 8px;
    padding: 10px 12px;
    font-weight: 650;
}}
QProgressBar#batchProgressBar {{
    min-height: 12px;
    max-height: 12px;
    color: transparent;
    background: {_color("surface_subtle")};
    border: none;
    border-radius: 6px;
    text-align: center;
}}
QProgressBar#batchProgressBar::chunk {{
    background: {_color("primary")};
    border-radius: 6px;
}}
QTableView#queueTable {{
    color: {_color("text")};
    background: {_color("surface")};
    alternate-background-color: {_color("canvas")};
    border: 1px solid {_color("border")};
    border-radius: 8px;
    selection-background-color: #dbeafe;
    selection-color: {_color("text")};
}}
QHeaderView::section {{
    color: {_color("text_muted")};
    background: {_color("surface_subtle")};
    border: none;
    border-bottom: 1px solid {_color("border")};
    padding: 8px;
    font-size: 10px;
    font-weight: 700;
}}
QLabel#emptyLabel {{
    color: {_color("text_faint")};
    padding: 3px;
}}
QFrame#statusBar {{
    background: {_color("surface")};
    border: none;
    border-top: 1px solid {_color("border")};
}}
QLabel#statusLabel {{
    color: {_color("text_muted")};
    font-size: 11px;
}}
QLabel#warningLabel {{
    color: {_color("danger")};
    background: {_color("danger_soft")};
    border: 1px solid #fecaca;
    border-radius: 8px;
    padding: 9px 12px;
}}
QLabel#sessionStatus {{ color: {_color("text_muted")}; }}
QDialog {{ background: {_color("canvas")}; }}
QSplitter::handle {{ background: transparent; }}
QSplitter::handle:horizontal {{ width: 12px; }}
QSplitter::handle:vertical {{ height: 12px; }}
QScrollArea {{ background: transparent; border: none; }}
QScrollBar:vertical {{
    background: transparent;
    width: 10px;
    margin: 2px;
}}
QScrollBar::handle:vertical {{
    background: {_color("border_strong")};
    border-radius: 4px;
    min-height: 30px;
}}
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{ height: 0; }}
"""
