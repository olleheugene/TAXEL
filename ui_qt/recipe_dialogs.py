"""
Recipe dialogs - info/save, import validation report, mode switch, DUT serial entry.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from typing import Optional, Tuple

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QVBoxLayout,
)

from core.language import language
from core.recipe import Recipe, ValidationReport

DIALOG_BG = "background-color: #1e293b; color: #f8fafc;"

INPUT_STYLE = """
    QLineEdit, QPlainTextEdit {
        background-color: #0f172a;
        border: 1px solid rgba(255, 255, 255, 0.2);
        border-radius: 6px;
        padding: 7px 10px;
        color: white;
    }
"""

PRIMARY_BTN = """
    QPushButton {
        background-color: #3b82f6; color: white; padding: 8px 20px;
        border-radius: 6px; font-weight: bold; border: 1px solid #60a5fa;
    }
    QPushButton:hover { background-color: #2563eb; }
"""

SECONDARY_BTN = """
    QPushButton {
        background-color: #334155; color: white; padding: 8px 18px;
        border-radius: 6px; border: 1px solid rgba(255,255,255,0.1);
    }
    QPushButton:hover { background-color: #475569; }
"""

DANGER_BTN = """
    QPushButton {
        background-color: rgba(239, 68, 68, 0.15); color: #f87171;
        border: 1px solid #ef4444; padding: 8px 18px; border-radius: 6px; font-weight: bold;
    }
    QPushButton:hover { background-color: #dc2626; color: white; }
"""

MONO = "font-family: 'Courier New', Menlo, Monaco, monospace;"


class RecipeInfoDialog(QDialog):
    """Edit a recipe's name, version, notes and lock state. Doubles as the
    confirmation screen before saving."""

    def __init__(self, recipe: Recipe, allow_lock_change: bool = True, parent=None):
        super().__init__(parent)
        self.recipe = recipe
        self.setWindowTitle(language.tr("recipe_info_title"))
        self.setMinimumWidth(560)
        self.setStyleSheet(DIALOG_BG)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        header = QLabel(language.tr("recipe_info_title"))
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #60a5fa;")
        layout.addWidget(header)

        form = QFormLayout()
        form.setSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        self.input_name = QLineEdit(recipe.name)
        self.input_name.setStyleSheet(INPUT_STYLE)
        self.input_version = QLineEdit(recipe.version)
        self.input_version.setStyleSheet(INPUT_STYLE)
        self.input_notes = QPlainTextEdit(recipe.notes)
        self.input_notes.setStyleSheet(INPUT_STYLE)
        self.input_notes.setMaximumHeight(90)

        form.addRow(language.tr("recipe_lbl_name"), self.input_name)
        form.addRow(language.tr("recipe_lbl_version"), self.input_version)
        form.addRow(language.tr("recipe_lbl_notes"), self.input_notes)

        self.chk_locked = QCheckBox(language.tr("recipe_chk_locked"))
        self.chk_locked.setChecked(recipe.locked)
        self.chk_locked.setEnabled(allow_lock_change)
        self.chk_locked.setStyleSheet("color: #fbbf24; font-weight: bold;")
        form.addRow("", self.chk_locked)

        layout.addLayout(form)

        # The fingerprint is there to be checked, not edited.
        fp = QLabel(language.tr("recipe_fingerprint_line", fp=recipe.fingerprint()))
        fp.setWordWrap(True)
        fp.setStyleSheet(f"color: #94a3b8; font-size: 11px; {MONO}")
        layout.addWidget(fp)

        steps = QLabel(language.tr("recipe_steps_line", count=len(recipe.steps),
                                modules=", ".join(s.module_id for s in recipe.steps)))
        steps.setWordWrap(True)
        steps.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(steps)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(language.tr("btn_cancel"))
        cancel.setStyleSheet(SECONDARY_BTN)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(language.tr("btn_ok"))
        ok.setStyleSheet(PRIMARY_BTN)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    def apply_to(self, recipe: Recipe) -> None:
        recipe.name = self.input_name.text().strip() or "Untitled Recipe"
        recipe.version = self.input_version.text().strip() or "0.1"
        recipe.notes = self.input_notes.toPlainText().strip()
        if self.chk_locked.isEnabled():
            recipe.locked = self.chk_locked.isChecked()


class ImportReportDialog(QDialog):
    """
    Show the import validation result and ask whether to apply it.
    Errors disable the Apply button - nothing passes silently.
    """

    def __init__(self, recipe: Optional[Recipe], report: ValidationReport, path: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(language.tr("recipe_import_title"))
        self.setMinimumWidth(660)
        self.setStyleSheet(DIALOG_BG)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        ok_to_apply = bool(recipe) and report.ok
        icon = "✅" if report.clean else ("⚠️" if report.ok else "❌")
        header = QLabel(language.tr("recipe_import_header", icon=icon, summary=report.summary()))
        header.setStyleSheet(
            f"font-size: 16px; font-weight: bold; color: {'#10b981' if report.clean else ('#fbbf24' if report.ok else '#f87171')};"
        )
        layout.addWidget(header)

        src = QLabel(path)
        src.setWordWrap(True)
        src.setStyleSheet(f"color: #94a3b8; font-size: 11px; {MONO}")
        layout.addWidget(src)

        if recipe:
            info = QLabel(
                language.tr("recipe_import_info", label=recipe.label,
                        count=len(recipe.steps), fp=recipe.short_fingerprint)
                + ("  " + language.tr("recipe_locked_badge") if recipe.locked else "")
            )
            info.setStyleSheet("font-weight: bold; color: #f8fafc;")
            layout.addWidget(info)

        detail = QPlainTextEdit(report.as_text())
        detail.setReadOnly(True)
        detail.setStyleSheet(INPUT_STYLE + " QPlainTextEdit { font-size: 12px; }")
        detail.setMinimumHeight(180)
        layout.addWidget(detail)

        if not report.ok:
            warn = QLabel(language.tr("recipe_import_error_hint"))
            warn.setWordWrap(True)
            warn.setStyleSheet("color: #f87171; font-size: 12px;")
            layout.addWidget(warn)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(language.tr("btn_close"))
        cancel.setStyleSheet(SECONDARY_BTN)
        cancel.clicked.connect(self.reject)
        btns.addWidget(cancel)

        self.btn_apply = QPushButton(language.tr("btn_apply"))
        self.btn_apply.setStyleSheet(PRIMARY_BTN if ok_to_apply else SECONDARY_BTN)
        self.btn_apply.setEnabled(ok_to_apply)
        self.btn_apply.clicked.connect(self.accept)
        btns.addWidget(self.btn_apply)
        layout.addLayout(btns)


class PasscodeDialog(QDialog):
    """Passcode prompt for entering engineer mode."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle(language.tr("dlg_passcode_title"))
        self.setMinimumWidth(420)
        self.setStyleSheet(DIALOG_BG)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        header = QLabel(language.tr("dlg_passcode_header"))
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #60a5fa;")
        layout.addWidget(header)

        hint = QLabel(language.tr("dlg_passcode_hint"))
        hint.setWordWrap(True)
        hint.setStyleSheet("color: #94a3b8; font-size: 12px;")
        layout.addWidget(hint)

        self.input_code = QLineEdit()
        self.input_code.setEchoMode(QLineEdit.Password)
        self.input_code.setPlaceholderText(language.tr("dlg_passcode_placeholder"))
        self.input_code.setStyleSheet(INPUT_STYLE)
        self.input_code.returnPressed.connect(self.accept)
        layout.addWidget(self.input_code)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(language.tr("btn_cancel"))
        cancel.setStyleSheet(SECONDARY_BTN)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(language.tr("btn_switch"))
        ok.setStyleSheet(PRIMARY_BTN)
        ok.clicked.connect(self.accept)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

    @property
    def passcode(self) -> str:
        return self.input_code.text()


class DutSerialDialog(QDialog):
    """
    DUT serial number entry. A barcode scanner types the value and sends Enter,
    so scanning goes straight through to confirmation.
    """

    def __init__(self, recipe_label: str = "", last_serial: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle(language.tr("dlg_dut_title"))
        self.setMinimumWidth(480)
        self.setStyleSheet(DIALOG_BG)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(12)

        header = QLabel(language.tr("dlg_dut_header"))
        header.setStyleSheet("font-size: 15px; font-weight: bold; color: #60a5fa;")
        layout.addWidget(header)

        if recipe_label:
            lbl = QLabel(language.tr("dlg_dut_recipe", label=recipe_label))
            lbl.setStyleSheet("color: #94a3b8; font-size: 12px;")
            layout.addWidget(lbl)

        self.input_serial = QLineEdit()
        self.input_serial.setPlaceholderText(language.tr("dlg_dut_placeholder"))
        self.input_serial.setStyleSheet(
            INPUT_STYLE + f" QLineEdit {{ font-size: 18px; {MONO} }}"
        )
        self.input_serial.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        self.input_serial.returnPressed.connect(self._on_return)
        layout.addWidget(self.input_serial)

        if last_serial:
            hint = QLabel(language.tr("dlg_dut_last", serial=last_serial))
            hint.setStyleSheet(f"color: #64748b; font-size: 11px; {MONO}")
            layout.addWidget(hint)

        self.error_label = QLabel("")
        self.error_label.setStyleSheet("color: #f87171; font-size: 12px;")
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        btns = QHBoxLayout()
        btns.addStretch()
        cancel = QPushButton(language.tr("btn_cancel"))
        cancel.setStyleSheet(SECONDARY_BTN)
        cancel.clicked.connect(self.reject)
        ok = QPushButton(language.tr("btn_start"))
        ok.setStyleSheet(PRIMARY_BTN)
        ok.clicked.connect(self._on_return)
        btns.addWidget(cancel)
        btns.addWidget(ok)
        layout.addLayout(btns)

        self.input_serial.setFocus()

    def _on_return(self):
        if not self.serial:
            self.error_label.setText(language.tr("dlg_dut_empty"))
            self.error_label.setVisible(True)
            return
        self.accept()

    @property
    def serial(self) -> str:
        return self.input_serial.text().strip()


def ask_dut_serial(recipe_label: str, last_serial: str = "", parent=None) -> Tuple[bool, str]:
    dlg = DutSerialDialog(recipe_label, last_serial, parent)
    if dlg.exec() == QDialog.Accepted:
        return True, dlg.serial
    return False, ""
