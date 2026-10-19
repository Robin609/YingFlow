"""A quiet editing surface; amber for actions, teal for confirmed work."""

STYLESHEET = """
* { font-family: 'Microsoft YaHei UI', 'Segoe UI'; font-size: 13px; }
QMainWindow, QDialog { background: #11151b; color: #e8edf5; }
QWidget { color: #e8edf5; }
QWidget#sidebar { background: #0c1015; border-right: 1px solid #242c37; }
QLabel#brand { font-size: 19px; font-weight: 700; color: #f4b76a; }
QLabel#eyebrow { color: #a7b4c5; font-size: 11px; letter-spacing: 2px; }
QLabel#pageTitle { font-size: 27px; font-weight: 700; }
QLabel#sectionTitle { font-size: 17px; font-weight: 600; }
QLabel#muted { color: #a1adbd; }
QLabel#statusLine { color: #92d4c6; background: #152c2b; padding: 10px 14px; border-radius: 6px; }
QLabel#hint { color: #b1bdce; background: #1a222e; padding: 12px; border-radius: 6px; }
QFrame#card { background: #191f28; border: 1px solid #2b3542; border-radius: 9px; }
QFrame#card QLabel { background: transparent; border: none; }
QFrame#recommendationCard { background: #191f28; border: 1px solid #303c4b; border-radius: 10px; }
QFrame#recommendationCard QLabel { background: transparent; border: none; }
QLabel#recommendationTitle { font-size: 21px; font-weight: 700; color: #f0f3f8; }
QLabel#recommendationAngle { font-size: 14px; color: #d3deeb; }
QLabel#recommendationSection { color: #edb46a; font-weight: 600; }
QLabel#recommendationStatus { color: #92d4c6; }
QFrame#recommendationEmpty { background: #151c25; border-radius: 10px; }
QLabel#nodeTitle { font-size: 17px; font-weight: 600; }
QPushButton { background: #253040; border: 1px solid #354257; padding: 8px 16px; border-radius: 6px; color: #e8edf5; }
QPushButton:hover { background: #33435a; border-color: #58708d; }
QPushButton:pressed { background: #1c2633; }
QPushButton:disabled { background: #1c222b; color: #8b96a5; border-color: #2b3340; }
QPushButton#primary { background: #edb46a; color: #191b20; border-color: #edb46a; font-weight: 700; }
QPushButton#primary:hover { background: #f7c787; }
QPushButton#nav { text-align: left; background: transparent; border: none; padding: 12px 16px; color: #aebccd; }
QPushButton#nav:checked { background: #24303e; color: #f4c182; border-left: 3px solid #edb46a; border-radius: 4px; }
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox {
    background: #141a23; color: #e8edf5; border: 1px solid #3b485b; border-radius: 5px; padding: 7px;
    selection-background-color: #425e7d;
}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus { border-color: #edb46a; }
QComboBox QAbstractItemView { background: #222d3b; color: #e8edf5; selection-background-color: #3e536c; }
QTableWidget, QListWidget { background: #151b24; alternate-background-color: #1a222e; gridline-color: #303b4b; border: 1px solid #303b4b; border-radius: 5px; }
QTableWidget::item { padding: 7px; border: none; }
QTableWidget::item:selected, QListWidget::item:selected { background: #34475f; color: #fff; }
QHeaderView::section { background: #202a37; color: #afbdce; border: none; border-bottom: 1px solid #3b485b; padding: 10px 8px; }
QScrollArea { background: transparent; border: none; }
QScrollArea > QWidget > QWidget { background: transparent; }
QScrollBar:vertical { background: #151b23; width: 10px; }
QScrollBar::handle:vertical { background: #425169; min-height: 30px; border-radius: 4px; }
QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
QTabWidget::pane { border: 1px solid #303b4b; background: #151b24; }
QTabBar::tab { background: #1b2430; color: #aebccd; padding: 11px 22px; }
QTabBar::tab:selected { background: #2a394c; color: #f4c182; }
QSplitter::handle { background: #242f3d; width: 3px; }
QProgressBar { background: #253040; border: none; border-radius: 3px; height: 6px; }
QProgressBar::chunk { background: #71c7b5; border-radius: 3px; }
QCheckBox { spacing: 8px; }
QCheckBox::indicator { width: 16px; height: 16px; }
QToolTip { color: #e8edf5; background: #253040; border: 1px solid #64758d; }
"""


def load_system_fonts():
    """Offscreen Qt has no native font discovery; use installed Windows fonts."""
    import os
    from pathlib import Path
    from PySide6.QtGui import QFontDatabase
    font_root = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts"
    for name in ("msyh.ttc", "msyhbd.ttc", "segoeui.ttf"):
        path = font_root / name
        if path.is_file():
            QFontDatabase.addApplicationFont(str(path))
