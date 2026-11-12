"""Modern dark theme: palette + stylesheet."""
from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

BG = "#14161b"
PANEL = "#1b1e25"
RAISED = "#232733"
BORDER = "#2c3140"
TEXT = "#e6e9f0"
MUTED = "#8b93a7"
ACCENT = "#5b8cff"

QSS = f"""
* {{ font-family: "Segoe UI Variable", "Segoe UI", sans-serif; font-size: 10pt; }}
QMainWindow, QWidget {{ background: {BG}; color: {TEXT}; }}
QLabel {{ background: transparent; }}

QTabWidget::pane {{ border: none; border-top: 1px solid {BORDER}; top: -1px; }}
QTabBar {{ background: {BG}; }}
QTabBar::tab {{
    background: transparent; color: {MUTED}; padding: 10px 16px; margin-right: 2px;
    border: none; border-bottom: 2px solid transparent;
}}
QTabBar::tab:hover {{ color: {TEXT}; background: {PANEL}; }}
QTabBar::tab:selected {{ color: {TEXT}; border-bottom: 2px solid {ACCENT}; }}
QTabBar::scroller {{ width: 22px; }}
QTabBar QToolButton {{ background: {PANEL}; border: none; }}

QPushButton {{
    background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 7px 16px;
}}
QPushButton:hover {{ background: #2c3244; border-color: #3b4258; }}
QPushButton:pressed {{ background: #1f2330; }}
QPushButton:disabled {{ color: #5b6275; background: {PANEL}; }}

QLineEdit, QSpinBox, QComboBox, QPlainTextEdit {{
    background: {PANEL}; color: {TEXT}; border: 1px solid {BORDER}; border-radius: 8px;
    padding: 6px 9px; selection-background-color: {ACCENT};
}}
QPlainTextEdit {{ padding: 8px; }}
QLineEdit:focus, QSpinBox:focus, QComboBox:focus, QPlainTextEdit:focus {{ border-color: {ACCENT}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox QAbstractItemView {{ background: {PANEL}; border: 1px solid {BORDER}; selection-background-color: {ACCENT}; }}
QSpinBox::up-button, QSpinBox::down-button {{ width: 0; }}

QCheckBox {{ spacing: 8px; color: {MUTED}; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border-radius: 5px; border: 1px solid #3b4258; background: {PANEL}; }}
QCheckBox::indicator:checked {{ background: {ACCENT}; border-color: {ACCENT}; }}

QTableWidget {{
    background: {PANEL}; alternate-background-color: {PANEL}; border: 1px solid {BORDER}; border-radius: 10px;
    gridline-color: transparent; selection-background-color: #26335a; selection-color: {TEXT};
}}
QTableWidget::item {{ padding: 5px 8px; border: none; }}
QHeaderView::section {{
    background: {BG}; color: {MUTED}; border: none; border-bottom: 1px solid {BORDER};
    padding: 8px; font-weight: 600;
}}

QScrollArea {{ border: none; background: transparent; }}
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar::handle:vertical {{ background: #343a4d; border-radius: 4px; min-height: 30px; }}
QScrollBar::handle:vertical:hover {{ background: #454d66; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:horizontal {{ background: #343a4d; border-radius: 4px; min-width: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

QToolTip {{ background: {RAISED}; color: {TEXT}; border: 1px solid {BORDER}; padding: 4px; }}
QMessageBox, QInputDialog, QDialog {{ background: {BG}; }}

QPushButton#start {{ background: #1f9d55; border: none; color: white; font-weight: 700; border-radius: 10px; }}
QPushButton#start:hover {{ background: #25b564; }}
QPushButton#stop {{ background: #d64545; border: none; color: white; font-weight: 700; border-radius: 10px; }}
QPushButton#stop:hover {{ background: #e65a5a; }}
QPushButton#start:disabled, QPushButton#stop:disabled {{ background: {RAISED}; color: #5b6275; }}
QPushButton#danger {{ color: #ff7b7b; }}
QPushButton#primary {{ background: {ACCENT}; border: none; color: white; font-weight: 600; }}
QPushButton#primary:hover {{ background: #7aa0ff; }}

QWidget#card {{ background: {PANEL}; border: 1px solid {BORDER}; border-radius: 12px; }}
QLabel#state {{ font-size: 15pt; font-weight: 700; }}
QLabel#muted {{ color: {MUTED}; }}
QLabel#rowname {{ font-size: 11pt; }}
"""


def apply(app: QApplication) -> None:
    app.setStyle("Fusion")
    pal = QPalette()
    pal.setColor(QPalette.Window, QColor(BG))
    pal.setColor(QPalette.WindowText, QColor(TEXT))
    pal.setColor(QPalette.Base, QColor(PANEL))
    pal.setColor(QPalette.AlternateBase, QColor(PANEL))
    pal.setColor(QPalette.Text, QColor(TEXT))
    pal.setColor(QPalette.Button, QColor(RAISED))
    pal.setColor(QPalette.ButtonText, QColor(TEXT))
    pal.setColor(QPalette.Highlight, QColor(ACCENT))
    pal.setColor(QPalette.HighlightedText, QColor("white"))
    pal.setColor(QPalette.Mid, QColor(BORDER))
    pal.setColor(QPalette.Midlight, QColor("#262b38"))
    pal.setColor(QPalette.ToolTipBase, QColor(RAISED))
    pal.setColor(QPalette.ToolTipText, QColor(TEXT))
    app.setPalette(pal)
    app.setFont(QFont("Segoe UI", 10))
    app.setStyleSheet(QSS)
