"""
Collapsible Live Console Widget for the GUI Dashboard.
Displays real-time logs and UART communication during test runs.
"""

import datetime
from typing import Optional

from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.language import language


class LiveConsoleWidget(QWidget):
    """
    Collapsible real-time log console panel for the main GUI dashboard.
    Displays live serial UART output and sequence execution logs.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._collapsed = False
        self._line_count = 0
        self.init_ui()

    def init_ui(self):
        self.setObjectName("LiveConsole")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 4, 0, 0)
        layout.setSpacing(0)

        # Header Bar
        self.header_bar = QFrame()
        self.header_bar.setStyleSheet("""
            QFrame {
                background-color: #0f172a;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
            }
        """)
        h_layout = QHBoxLayout(self.header_bar)
        h_layout.setContentsMargins(10, 5, 10, 5)
        h_layout.setSpacing(8)

        self.btn_toggle = QPushButton("▼")
        self.btn_toggle.setFixedSize(24, 24)
        self.btn_toggle.setStyleSheet("""
            QPushButton {
                background-color: transparent; color: #94a3b8; border: none; font-weight: bold; font-size: 11px;
            }
            QPushButton:hover { color: #f8fafc; }
        """)
        self.btn_toggle.clicked.connect(self.toggle_collapse)
        h_layout.addWidget(self.btn_toggle)

        self.lbl_title = QLabel(f"🖥️ {language.tr('console_title')}")
        self.lbl_title.setStyleSheet("font-size: 12px; font-weight: bold; color: #38bdf8;")
        h_layout.addWidget(self.lbl_title)

        self.lbl_badge = QLabel("0 lines")
        self.lbl_badge.setStyleSheet("""
            QLabel {
                background-color: rgba(56, 189, 248, 0.15);
                color: #7dd3fc;
                border-radius: 4px;
                padding: 1px 6px;
                font-size: 11px;
                font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
            }
        """)
        h_layout.addWidget(self.lbl_badge)
        h_layout.addStretch(1)

        self.chk_autoscroll = QCheckBox(language.tr("console_autoscroll"))
        self.chk_autoscroll.setChecked(True)
        self.chk_autoscroll.setStyleSheet("color: #94a3b8; font-size: 11px;")
        h_layout.addWidget(self.chk_autoscroll)

        self.btn_copy = QPushButton(f"📋 {language.tr('console_copy')}")
        self.btn_copy.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #cbd5e1; border: 1px solid rgba(255,255,255,0.1);
                border-radius: 4px; padding: 3px 8px; font-size: 11px;
            }
            QPushButton:hover { background-color: #334155; color: white; }
        """)
        self.btn_copy.clicked.connect(self.copy_logs)
        h_layout.addWidget(self.btn_copy)

        self.btn_clear = QPushButton(f"🧹 {language.tr('console_clear')}")
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #cbd5e1; border: 1px solid rgba(255,255,255,0.1);
                border-radius: 4px; padding: 3px 8px; font-size: 11px;
            }
            QPushButton:hover { background-color: #334155; color: white; }
        """)
        self.btn_clear.clicked.connect(self.clear_logs)
        h_layout.addWidget(self.btn_clear)

        layout.addWidget(self.header_bar)

        # Log Content View
        self.log_edit = QPlainTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setMaximumBlockCount(5000)
        self.log_edit.setStyleSheet("""
            QPlainTextEdit {
                background-color: #050811;
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-top: none;
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                padding: 8px;
                font-family: Menlo, Consolas, 'Courier New', monospace;
                font-size: 11px;
                line-height: 1.35;
            }
        """)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.log_edit.setFont(font)
        layout.addWidget(self.log_edit, stretch=1)

    def append_log(self, message: str, step_id: str = ""):
        self._line_count += 1
        self.lbl_badge.setText(f"{self._line_count} lines")

        ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
        prefix = f"[{ts}]"
        if step_id:
            formatted = f"{prefix} [{step_id}] {message}"
        else:
            formatted = f"{prefix} {message}"

        self.log_edit.appendPlainText(formatted)
        if self.chk_autoscroll.isChecked():
            self.log_edit.moveCursor(QTextCursor.End)

    def clear_logs(self):
        self.log_edit.clear()
        self._line_count = 0
        self.lbl_badge.setText("0 lines")

    def copy_logs(self):
        text = self.log_edit.toPlainText()
        if text:
            QApplication.clipboard().setText(text)

    def toggle_collapse(self):
        self._collapsed = not self._collapsed
        if self._collapsed:
            self.log_edit.hide()
            self.btn_toggle.setText("▲")
            self.lbl_title.setText(f"🖥️ {language.tr('console_expand')}")
            self.header_bar.setStyleSheet("""
                QFrame {
                    background-color: #0f172a;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-radius: 8px;
                }
            """)
            self.setFixedHeight(36)
        else:
            self.setMinimumHeight(80)
            self.setMaximumHeight(16777215)
            self.log_edit.show()
            self.btn_toggle.setText("▼")
            self.lbl_title.setText(f"🖥️ {language.tr('console_title')}")
            self.header_bar.setStyleSheet("""
                QFrame {
                    background-color: #0f172a;
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-top-left-radius: 8px;
                    border-top-right-radius: 8px;
                }
            """)

    def update_language(self):
        if self._collapsed:
            self.lbl_title.setText(f"🖥️ {language.tr('console_expand')}")
        else:
            self.lbl_title.setText(f"🖥️ {language.tr('console_title')}")
        self.chk_autoscroll.setText(language.tr("console_autoscroll"))
        self.btn_copy.setText(f"📋 {language.tr('console_copy')}")
        self.btn_clear.setText(f"🧹 {language.tr('console_clear')}")
