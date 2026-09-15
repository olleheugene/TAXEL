"""
Interactive Serial Terminal Dialog (PySide6).
Provides a real-time serial monitor & command transmitter for testing DUT UART.
"""

import datetime
import os
import sys
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, QTimer, Signal, Slot, QEvent
from PySide6.QtGui import QFont, QFontDatabase, QTextCursor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from core.language import language
from cards.serial_session import serial_registry, SerialSession, SerialSessionError


class SerialTerminalDialog(QDialog):
    """Interactive serial monitor dialog."""

    def __init__(self, criteria: Dict[str, Any], parent=None):
        super().__init__(parent)
        self.criteria = criteria or {}
        self.port = str(self.criteria.get("port", "") or "").strip()
        default_target = serial_registry.default_target or {}
        if not self.port:
            self.port = str(default_target.get("port", "") or "").strip()
        self.baudrate = int(self.criteria.get("baudrate") or default_target.get("baudrate", 115200))
        self.session: Optional[SerialSession] = None
        self.cmd_history: List[str] = []
        self.history_idx: int = -1

        self.setWindowTitle(f"📟 {language.tr('terminal_title', default='Serial Terminal')} - {self.port or 'DUT'}")
        self.resize(760, 540)
        self.setMinimumSize(560, 380)
        self.setStyleSheet("background-color: #0f172a; color: #f8fafc;")

        self.init_ui()
        self.init_connection()

    def init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(10)

        # Top Bar: Connection Info & Controls
        top_bar = QHBoxLayout()
        self.status_indicator = QLabel("● CONNECTING")
        self.status_indicator.setStyleSheet("color: #f59e0b; font-weight: bold; font-size: 12px;")
        top_bar.addWidget(self.status_indicator)

        self.lbl_port_info = QLabel(f"{self.port or 'No Port'} @ {self.baudrate} bps")
        self.lbl_port_info.setStyleSheet("color: #94a3b8; font-size: 12px; margin-left: 8px;")
        top_bar.addWidget(self.lbl_port_info)
        top_bar.addStretch(1)

        self.chk_timestamps = QCheckBox(language.tr("terminal_timestamps", default="Timestamps"))
        self.chk_timestamps.setChecked(True)
        self.chk_timestamps.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        top_bar.addWidget(self.chk_timestamps)

        self.chk_autoscroll = QCheckBox(language.tr("terminal_autoscroll", default="Auto-scroll"))
        self.chk_autoscroll.setChecked(True)
        self.chk_autoscroll.setStyleSheet("color: #cbd5e1; font-size: 12px;")
        top_bar.addWidget(self.chk_autoscroll)

        self.btn_clear = QPushButton(language.tr("terminal_clear", default="Clear"))
        self.btn_clear.setStyleSheet("""
            QPushButton {
                background-color: #1e293b; color: #94a3b8; border: 1px solid rgba(255,255,255,0.1);
                border-radius: 4px; padding: 4px 10px; font-size: 12px;
            }
            QPushButton:hover { background-color: #334155; color: white; }
        """)
        self.btn_clear.clicked.connect(self.clear_terminal)
        top_bar.addWidget(self.btn_clear)

        layout.addLayout(top_bar)

        # Terminal Screen (QPlainTextEdit)
        self.term_edit = QPlainTextEdit()
        self.term_edit.setReadOnly(True)
        self.term_edit.setStyleSheet("""
            QPlainTextEdit {
                background-color: #050811;
                color: #34d399;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 10px;
                font-family: Menlo, Consolas, 'Courier New', monospace;
                font-size: 12px;
                line-height: 1.4;
            }
        """)
        font = QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont)
        self.term_edit.setFont(font)
        layout.addWidget(self.term_edit, stretch=1)

        # Bottom Command Input Bar
        input_bar = QHBoxLayout()
        input_bar.setSpacing(8)

        lbl_tx = QLabel("TX >")
        lbl_tx.setStyleSheet("color: #38bdf8; font-family: 'Menlo', 'Monaco', 'Courier New', monospace; font-weight: bold;")
        input_bar.addWidget(lbl_tx)

        self.cmd_input = QLineEdit()
        self.cmd_input.setPlaceholderText(language.tr("terminal_input_placeholder", default="Enter command to send... (Press Enter)"))
        self.cmd_input.setStyleSheet("""
            QLineEdit {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 8px 12px;
                font-family: Menlo, Consolas, monospace;
                font-size: 13px;
            }
            QLineEdit:focus {
                border-color: #38bdf8;
            }
        """)
        self.cmd_input.returnPressed.connect(self.send_command)
        self.cmd_input.installEventFilter(self)
        input_bar.addWidget(self.cmd_input, stretch=1)

        self.cbo_ending = QComboBox()
        self.cbo_ending.addItems(["\\r\\n (CRLF)", "\\n (LF)", "\\r (CR)", "None"])
        self.cbo_ending.setStyleSheet("""
            QComboBox {
                background-color: #1e293b; color: #cbd5e1;
                border: 1px solid rgba(255,255,255,0.15); border-radius: 6px;
                padding: 6px 10px; font-size: 12px;
            }
        """)
        input_bar.addWidget(self.cbo_ending)

        self.btn_send = QPushButton(language.tr("terminal_send", default="Send"))
        self.btn_send.setStyleSheet("""
            QPushButton {
                background-color: #0284c7; color: white; font-weight: bold;
                border-radius: 6px; padding: 8px 16px; border: 1px solid #38bdf8;
            }
            QPushButton:hover { background-color: #0369a1; }
            QPushButton:pressed { background-color: #075985; }
        """)
        self.btn_send.clicked.connect(self.send_command)
        input_bar.addWidget(self.btn_send)

        layout.addLayout(input_bar)

    def init_connection(self):
        # Poll timer for reading incoming serial data
        self.read_timer = QTimer(self)
        self.read_timer.setInterval(40)  # ~25 fps poll
        self.read_timer.timeout.connect(self._poll_serial)

        if not self.port or self.port == "mock" or "mock" in self.port.lower():
            self.status_indicator.setText("● MOCK MODE")
            self.status_indicator.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
            self.append_line("[TERMINAL] Opened in simulated mock mode.")
            self.append_line("[DUT] System Ready (Mock v1.0). Type commands like 'help', 'ping', 'version'.")
            self.read_timer.start()
            return

        try:
            # Borrow shared session if already open, or open a session
            self.session = serial_registry.borrow(self.port, self.baudrate, log_callback=None)
            self.status_indicator.setText("● CONNECTED")
            self.status_indicator.setStyleSheet("color: #10b981; font-weight: bold; font-size: 12px;")
            self.append_line(f"[TERMINAL] Connected to {self.port} @ {self.baudrate} bps.")
            self.read_timer.start()
        except Exception as e:
            self.status_indicator.setText("○ OFFLINE (Mock Fallback)")
            self.status_indicator.setStyleSheet("color: #ef4444; font-weight: bold; font-size: 12px;")
            self.append_line(f"[TERMINAL] Could not open {self.port}: {e}")
            self.append_line("[TERMINAL] Falling back to simulation view.")
            self.read_timer.start()

    def append_line(self, line: str, is_tx: bool = False):
        if self.chk_timestamps.isChecked():
            ts = datetime.datetime.now().strftime("%H:%M:%S.%f")[:-3]
            formatted = f"[{ts}] {line}"
        else:
            formatted = line

        self.term_edit.appendPlainText(formatted)
        if self.chk_autoscroll.isChecked():
            self.term_edit.moveCursor(QTextCursor.End)

    def clear_terminal(self):
        self.term_edit.clear()

    def send_command(self):
        cmd = self.cmd_input.text()
        if not cmd:
            return

        self.cmd_history.append(cmd)
        self.history_idx = len(self.cmd_history)
        self.cmd_input.clear()

        # Append ending
        ending_mode = self.cbo_ending.currentIndex()
        if ending_mode == 0:
            payload = cmd + "\r\n"
        elif ending_mode == 1:
            payload = cmd + "\n"
        elif ending_mode == 2:
            payload = cmd + "\r"
        else:
            payload = cmd

        self.append_line(f">> {cmd}", is_tx=True)

        if self.session and not self.session.mock:
            try:
                self.session.write(payload.encode("utf-8", errors="replace"))
            except Exception as e:
                self.append_line(f"[ERR] Send failed: {e}")
        else:
            # Mock echo / simulation response
            self._mock_respond(cmd)

    def _mock_respond(self, cmd: str):
        cmd_clean = cmd.strip().lower()
        if cmd_clean in ("help", "?"):
            self.append_line("[DUT] Available commands: help, ping, version, info, reset")
        elif cmd_clean == "ping":
            self.append_line("[DUT] PONG (0.42ms)")
        elif cmd_clean in ("version", "ver"):
            self.append_line("[DUT] nRF Application v2.4.1 (Built Sep 2026, GCC 12.3)")
        elif cmd_clean == "info":
            self.append_line("[DUT] SoC: nRF52840-QIAA-R | RAM: 256KB | Flash: 1024KB | VDD: 3.3V")
        elif cmd_clean == "reset":
            self.append_line("[DUT] Resetting system...")
            QTimer.singleShot(300, lambda: self.append_line("[BOOT] nRF52840 System Initialized"))
            QTimer.singleShot(600, lambda: self.append_line("[READY] DUT ready for commands"))
        else:
            self.append_line(f"[DUT] OK: {cmd}")

    def _poll_serial(self):
        if not self.session or self.session.mock:
            return

        try:
            line = self.session.readline(timeout_sec=0.01)
            if line:
                self.append_line(f"<< {line}")
        except Exception:
            pass

    def eventFilter(self, obj, event):
        if obj == self.cmd_input and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            if key == Qt.Key_Up:
                if self.cmd_history and self.history_idx > 0:
                    self.history_idx -= 1
                    self.cmd_input.setText(self.cmd_history[self.history_idx])
                return True
            elif key == Qt.Key_Down:
                if self.cmd_history and self.history_idx < len(self.cmd_history) - 1:
                    self.history_idx += 1
                    self.cmd_input.setText(self.cmd_history[self.history_idx])
                elif self.history_idx >= len(self.cmd_history) - 1:
                    self.history_idx = len(self.cmd_history)
                    self.cmd_input.clear()
                return True
        return super().eventFilter(obj, event)

    def closeEvent(self, event):
        if self.read_timer.isActive():
            self.read_timer.stop()
        super().closeEvent(event)
