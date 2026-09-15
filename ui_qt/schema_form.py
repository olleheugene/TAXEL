"""
Schema-driven settings dialog (Qt).

Renders the FieldSpec list produced by core.schema as widgets. This file knows
nothing about any specific module - every module, including one a third party
drops in later, goes through the same path.

The previous structure:
    on_settings_clicked()
        if module_id == "config_serial_interface":  -> SerialConfigSetupDialog
        elif module_id == "firmware_flasher":       -> FirmwareConfigSetupDialog
        else:                                       -> CriteriaDialog (forced every value into a QDoubleSpinBox)

Because the generic dialog could only handle floats, any module using string or
boolean criteria needed its own dialog added to the core to work at all.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from typing import Any, Callable, Dict, List, Optional

import html
import re

from PySide6.QtCore import QSize, Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.language import language
from core.schema import FieldSpec, describe_actions, normalize_schema
from ui_qt.spinbox import with_steppers
from cards.ppk_session import ppk_registry
from cards.serial_session import serial_registry

# --------------------------------------------------------------------- styles
INPUT_STYLE = """
    background-color: #0f172a;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 6px;
    padding: 6px;
    color: white;
"""

MONO_INPUT_STYLE = """
    QLineEdit {
        background-color: #0f172a;
        border: 1px solid rgba(255, 255, 255, 0.2);
        border-radius: 6px;
        padding: 7px 10px;
        color: #60a5fa;
        font-family: 'Courier New', 'Menlo', 'Monaco', monospace;
        font-size: 13px;
        font-weight: bold;
    }
    QLineEdit:disabled {
        background-color: #334155;
        color: #94a3b8;
    }
"""

SECONDARY_BTN_STYLE = """
    QPushButton {
        background-color: #334155;
        color: white;
        padding: 6px 14px;
        border-radius: 6px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    QPushButton:hover { background-color: #475569; }
    QPushButton:pressed {
        background-color: #1e293b;
        padding-top: 8px;
        padding-left: 16px;
    }
"""

ACTION_BTN_STYLE = """
    QPushButton {
        background-color: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid #3b82f6;
        padding: 10px;
        border-radius: 6px;
        font-weight: bold;
    }
    QPushButton:hover {
        background-color: #2563eb;
        color: white;
        border-color: #60a5fa;
    }
    QPushButton:pressed {
        background-color: #1d4ed8;
        color: white;
        border-color: #93c5fd;
        padding-top: 12px;
    }
"""

PRIMARY_BTN_STYLE = """
    QPushButton {
        background-color: #3b82f6;
        color: white;
        padding: 8px 20px;
        border-radius: 6px;
        font-weight: bold;
        border: 1px solid #60a5fa;
    }
    QPushButton:hover { background-color: #2563eb; }
    QPushButton:pressed {
        background-color: #1d4ed8;
        border-color: #93c5fd;
        padding-top: 10px;
        padding-left: 22px;
    }
"""


def _combo_style(arrow_svg_path: str) -> str:
    return f"""
        QComboBox {{
            background-color: #0f172a;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            padding: 6px 26px 6px 12px;
            color: white;
        }}
        QComboBox::drop-down {{
            subcontrol-origin: padding;
            subcontrol-position: center right;
            width: 20px;
            border: none;
            background: transparent;
            margin-right: 6px;
        }}
        QComboBox::down-arrow {{
            image: url('{arrow_svg_path}');
            width: 10px;
            height: 10px;
        }}
        QComboBox QAbstractItemView {{
            background-color: #1e293b;
            color: white;
            selection-background-color: #3b82f6;
            border: 1px solid rgba(255, 255, 255, 0.2);
            border-radius: 6px;
            outline: 0;
        }}
    """


# The editable combo (port and PPK2 device pickers) needs its inner line edit
# sized explicitly - see _style_combo for the measurement behind these.
EDITABLE_COMBO_MIN_HEIGHT = 32
EDITABLE_COMBO_LINE_MIN_HEIGHT = 20
EDITABLE_COMBO_LINE_STYLE = """
    QLineEdit {
        background: transparent;
        border: none;
        color: white;
        padding: 0px;
        margin: 0px;
    }
"""


# Vertical gap between form rows. Deliberately tight: a field and its help are
# two rows, so this gap appears twice per setting and dominates the dialog's
# height. The help labels pull themselves up against their field with a negative
# top margin, which is what keeps a setting reading as one block.
FORM_ROW_SPACING = 5

HELP_LABEL_STYLE = """
    color: #94a3b8;
    font-size: 11px;
    margin-top: -2px;
    margin-bottom: 2px;
"""

WARNING_LABEL_STYLE = """
    color: #fbbf24;
    font-size: 11px;
    margin-top: -1px;
"""

# Dialog width. It follows the window it opens over, inset by this much, and
# never goes below the floor - a settings screen that is wider than its own
# window looks like it belongs to something else.
DIALOG_WIDTH_INSET = 60
DIALOG_MIN_WIDTH = 560
DIALOG_MAX_WIDTH = 914
DIALOG_FALLBACK_WIDTH = 720

# A field whose value no longer matches what the dialog opened with is marked,
# because the value can change without being touched deliberately: a scroll
# wheel over a spin box or a combo moves it, and nothing else would say so.
CHANGED_COLOR = "#fbbf24"
CHANGED_LABEL_STYLE = f"color: {CHANGED_COLOR}; font-weight: bold;"
CHANGED_MARKER = "\u25cf "      # a filled dot before the label text

SCROLL_AREA_STYLE = """
    QScrollArea { background: transparent; border: none; }
    QScrollArea > QWidget > QWidget { background: transparent; }
    QScrollBar:vertical {
        background: transparent;
        width: 10px;
        margin: 0px;
    }
    QScrollBar::handle:vertical {
        background: rgba(148, 163, 184, 0.5);
        border-radius: 5px;
        min-height: 30px;
    }
    QScrollBar::handle:vertical:hover { background: rgba(148, 163, 184, 0.8); }
    QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height: 0px; }
    QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background: none; }
"""


def _range_note(spec: FieldSpec) -> str:
    """
    "800-5000 mV" for a number field that declares both bounds.

    Derived from the spec rather than written into each help string, so it
    cannot drift from the limits the widget actually enforces and does not have
    to be repeated in every language file.
    """
    if spec.type != "number" or spec.minimum is None or spec.maximum is None:
        return ""
    decimals = spec.decimals
    if decimals is None:
        decimals = 0 if float(spec.minimum).is_integer() and float(spec.maximum).is_integer() else 2
    low = f"{float(spec.minimum):.{decimals}f}"
    high = f"{float(spec.maximum):.{decimals}f}"
    unit = f" {spec.unit}" if spec.unit else ""
    step = f", step {spec.step:g}" if spec.step else ""
    return f"{low}-{high}{unit}{step}"


class _WrapLabel(QLabel):
    """
    A word-wrapping label that takes exactly the height its text needs at the
    width it actually got.

    Two separate problems made this necessary, and one of them was introduced by
    the fix for the other.

    A plain wordWrap QLabel does not advertise heightForWidth on its size
    policy, so QFormLayout sized the row from a single line and clipped the
    rest. Setting the flag was not enough either: the layout still takes
    `sizeHint()`, and QLabel computes that at an internal heuristic width, which
    for a two-line help text came out at 75 px where 15 px was needed. And a
    `MinimumExpanding` vertical policy then absorbed all the dialog's slack,
    growing one help label to 304 px and pushing it far away from the field it
    describes.

    So the hint is reported for the current width, the vertical policy is
    `Minimum` so it never claims slack, and a resize recomputes it. Measured on
    the same text: 15 px in an 880 px dialog, 30 px at 620 px where it wraps to
    two lines.
    """

    def __init__(self, text: str = "", style: str = ""):
        super().__init__(text)
        self.setWordWrap(True)
        if style:
            self.setStyleSheet(style)
        policy = self.sizePolicy()
        policy.setHeightForWidth(True)
        policy.setVerticalPolicy(QSizePolicy.Minimum)
        self.setSizePolicy(policy)
        self._wrapped_height = -1

    def sizeHint(self) -> QSize:
        width = self.width()
        if width > 0:
            return QSize(width, self.heightForWidth(width))
        return super().sizeHint()

    def minimumSizeHint(self) -> QSize:
        # Zero, so the tall narrow-width hint cannot act as a floor.
        return QSize(0, 0)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        height = self.heightForWidth(self.width()) if self.width() > 0 else -1
        # Guarded: updateGeometry() triggers a relayout, so only when the
        # wrapped height actually changed.
        if height != self._wrapped_height:
            self._wrapped_height = height
            self.updateGeometry()


class _FieldLabel(QLabel):
    """
    A field's title, which can be clicked.

    It has to be a Python subclass. Passing a string to `QFormLayout.addRow`
    makes Qt create the QLabel in C++, and PySide only dispatches a virtual to
    Python for objects whose Python class overrides it - so assigning
    `label.mousePressEvent = handler` on such a wrapper is accepted and then
    never called. That mistake looked correct in a test that invoked the
    handler directly and did nothing at all under a real click.
    """

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


def _restore_value(widget: QWidget, value: Any) -> bool:
    """
    Put a value back into whichever input widget holds it.

    A single function rather than a setter per field type: every widget this
    dialog builds is one of these five classes, so the mapping is complete and
    a new field type that reuses them needs nothing here.

    For an enum the stored value is matched by item **data** first - the same
    order read_enum() uses - because a label that happens to look numeric must
    not be parsed back into a different value.
    """
    try:
        if isinstance(widget, QCheckBox):
            widget.setChecked(bool(value))
            return True
        if isinstance(widget, (QSpinBox, QDoubleSpinBox)):
            widget.setValue(type(widget.value())(value))
            return True
        if isinstance(widget, QComboBox):
            index = widget.findData(value)
            if index < 0:
                index = widget.findText(str(value))
            if index >= 0:
                widget.setCurrentIndex(index)
            elif widget.isEditable():
                widget.setCurrentText("" if value is None else str(value))
            else:
                return False
            return True
        if isinstance(widget, QLineEdit):
            widget.setText("" if value is None else str(value))
            return True
    except Exception:
        return False
    return False


def _wrapped_label(text: str, style: str) -> QLabel:
    """A help or warning label sized to its wrapped text. See _WrapLabel."""
    return _WrapLabel(text, style)


def _file_filter(spec: FieldSpec) -> str:
    """Turn an accept declaration into a Qt file-dialog filter."""
    all_files = f"{language.tr('filter_all_files')} (*)"
    if not spec.accept:
        return all_files
    patterns = []
    for item in spec.accept:
        item = str(item).strip()
        patterns.append(f"*{item}" if item.startswith(".") else item)
    return f"{language.tr('filter_accepted')} ({' '.join(patterns)});;{all_files}"


_BOLD_RE = re.compile(r"\*\*(.+?)\*\*", re.S)


def _help_to_rich(text: str) -> str:
    """
    Render `**emphasis**` in a module's help text as actual bold.

    A plain QLabel showed the asterisks literally, which is how a note about
    the *average* ended up reading as `**average**` in the dialog. The markers
    stay in the module source, where they are the natural way to write it, and
    are converted here.

    The text is escaped first: help strings are authored in module code rather
    than typed by a user, but a stray `<` or `&` would still silently swallow
    the rest of the line once the label is in rich-text mode.
    """
    escaped = html.escape(text, quote=False)
    return _BOLD_RE.sub(r"<b>\1</b>", escaped)


class FieldWidget:
    """The widget for one FieldSpec, plus reading its value."""

    def __init__(self, spec: FieldSpec, widget: QWidget, getter: Callable[[], Any],
                 container: Optional[QWidget] = None, enable_target: Optional[QWidget] = None,
                 extra_rows: Optional[List[QWidget]] = None):
        self.spec = spec
        self.widget = widget
        self.container = container or widget
        self.enable_target = enable_target or widget
        # Widgets the dialog must place in their **own** form rows, below the
        # field. Stacking them inside the field's container instead let the form
        # give that container one row's height and the container's layout then
        # squeezed its children on top of each other - which is how the port
        # picker ended up with its stale-port warning drawn over the combo.
        self.extra_rows: List[QWidget] = list(extra_rows or [])
        self.label_widget: Optional[QWidget] = None
        self.is_active: bool = True
        self._getter = getter

    def value(self) -> Any:
        return self._getter()


class SchemaCriteriaDialog(QDialog):
    """
    Builds a settings screen purely from what the module declares.
    There is no branching on module_id.
    """

    def __init__(
        self,
        module_inst,
        current_criteria: dict,
        arrow_svg_path: str = "",
        lang: str = "en",
        translator: Optional[Callable[[str], str]] = None,
        combo_popup_setup: Optional[Callable[[QComboBox], None]] = None,
        parent=None,
    ):
        super().__init__(parent)
        self.module = module_inst
        self.lang = lang
        self._tr = translator or language.tr
        self._arrow = arrow_svg_path
        self._combo_popup_setup = combo_popup_setup
        self.fields: Dict[str, FieldWidget] = {}

        mod_name = module_inst.get_localized("name", lang)
        icon = "⚙️"
        self.setWindowTitle(f"{icon} " + self._tr_fmt("dlg_setup_title", name=mod_name))
        # Sized against the window it opens over, a little narrower so it reads
        # as a panel belonging to that window rather than a second one that
        # happens to be a different width. Falls back to a fixed width when
        # there is no parent - the CLI's dialogs and the tests.
        self.setMinimumWidth(DIALOG_MIN_WIDTH)
        self.setMaximumWidth(DIALOG_MAX_WIDTH)
        # A floor for the height, so the dialog cannot be dragged down to a
        # sliver where the scrollbar is the only thing left. Small enough that a
        # short laptop screen can still shrink it.
        self.setMinimumHeight(260)
        self._preferred_width = self._width_for(parent)
        self.setStyleSheet("background-color: #1e293b; color: #f8fafc;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 16, 20, 16)
        layout.setSpacing(10)

        header = QLabel(f"{icon} " + self._tr_fmt("dlg_setup_title", name=mod_name))
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #60a5fa;")
        layout.addWidget(header)

        specs = normalize_schema(
            module_inst,
            current_criteria=current_criteria,
            lang=lang,
            translator=self._tr,
        )

        form = QFormLayout()
        # Set separately. A field's help sits in its **own** row, so a single
        # spacing value put the same gap between a field and its own help as
        # between two unrelated fields - which read as one loose column rather
        # than as grouped settings. The vertical gap is tightened and the label
        # column keeps its breathing room.
        form.setVerticalSpacing(FORM_ROW_SPACING)
        form.setHorizontalSpacing(12)
        form.setFieldGrowthPolicy(QFormLayout.AllNonFixedFieldsGrow)

        for spec in specs:
            if spec.section is not None:
                form.addRow(self._section_rule(spec.section))
            fw = self._build_field(spec)
            self.fields[spec.key] = fw
            # For framework bool fields the checkbox carries its own label.
            if spec.framework and spec.type == "bool":
                form.addRow("", fw.container)
            else:
                lbl = _FieldLabel(f"{spec.label_with_unit}:")
                fw.label_widget = lbl
                form.addRow(lbl, fw.container)
            for extra in fw.extra_rows:
                form.addRow("", extra)
            # The allowed range is shown with the help rather than typed into
            # it: an operator otherwise finds the limits by being silently
            # clamped, and a hand-written range goes stale the moment the
            # module changes its bounds.
            help_parts = [p for p in (_range_note(spec), spec.help_text) if p]
            if help_parts:
                hint = _wrapped_label(_help_to_rich("  ".join(help_parts)),
                                      HELP_LABEL_STYLE)
                hint.setTextFormat(Qt.RichText)
                fw.extra_rows.append(hint)
                form.addRow("", hint)

        # The form goes in a scroll area. Without one, shrinking the dialog
        # vertically does not hide anything - it squeezes every row, and wrapped
        # help text and the port picker lose their content to clipping. A scroll
        # area lets the content keep the height it asked for and moves the
        # shortfall into a scrollbar, which is the only outcome that stays
        # readable at any window size or in any language.
        form_host = QWidget()
        form_host.setLayout(form)
        scroll = QScrollArea()
        scroll.setWidget(form_host)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        scroll.setStyleSheet(SCROLL_AREA_STYLE)
        layout.addWidget(scroll, 1)

        # Wire dependent fields (e.g. the pre-command checkbox enabling its input)
        for key, fw in self.fields.items():
            if not fw.spec.enabled_by:
                continue
            source = self.fields.get(fw.spec.enabled_by)
            if source and isinstance(source.widget, QCheckBox):
                fw.enable_target.setEnabled(source.widget.isChecked())
                source.widget.toggled.connect(fw.enable_target.setEnabled)

        # Wire dynamically added sub-fields (e.g. fw_file_path + Netcore field)
        for key, fw in self.fields.items():
            if not fw.spec.added_by:
                continue
            parent_fw = self.fields.get(fw.spec.added_by)
            if not parent_fw:
                continue

            btn_text = fw.spec.add_button_text or f"➕ {fw.spec.label.split()[0]}"
            btn_add = QPushButton(btn_text)
            btn_add.setToolTip(language.tr("tooltip_add_netcore") if hasattr(language, "tr") else "Add Netcore")
            btn_add.setStyleSheet("""
                QPushButton {
                    background-color: rgba(59, 130, 246, 0.15);
                    color: #60a5fa;
                    border: 1px solid #3b82f6;
                    padding: 6px 12px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #2563eb;
                    color: white;
                    border-color: #60a5fa;
                }
                QPushButton:pressed {
                    background-color: #1d4ed8;
                }
            """)
            if parent_fw.container.layout():
                parent_fw.container.layout().addWidget(btn_add)

            btn_remove = QPushButton("✕")
            btn_remove.setToolTip(language.tr("tooltip_remove_netcore") if hasattr(language, "tr") else "Remove Netcore")
            btn_remove.setStyleSheet("""
                QPushButton {
                    background-color: rgba(239, 68, 68, 0.15);
                    color: #ef4444;
                    border: 1px solid #ef4444;
                    padding: 6px 10px;
                    border-radius: 6px;
                    font-weight: bold;
                }
                QPushButton:hover {
                    background-color: #dc2626;
                    color: white;
                    border-color: #ef4444;
                }
                QPushButton:pressed {
                    background-color: #b91c1c;
                }
            """)
            if fw.container.layout():
                fw.container.layout().addWidget(btn_remove)

            def set_child_visible(visible: bool, child_fw=fw, p_btn=btn_add):
                child_fw.is_active = visible
                if hasattr(child_fw, "label_widget") and child_fw.label_widget:
                    child_fw.label_widget.setVisible(visible)
                child_fw.container.setVisible(visible)
                for extra in child_fw.extra_rows:
                    extra.setVisible(visible)
                p_btn.setVisible(not visible)

            def on_add_clicked(_checked=False, child_fw=fw, p_btn=btn_add):
                set_child_visible(True, child_fw, p_btn)
                if hasattr(child_fw.widget, "setFocus"):
                    child_fw.widget.setFocus()

            def on_remove_clicked(_checked=False, child_fw=fw, p_btn=btn_add):
                if hasattr(child_fw.widget, "setText"):
                    child_fw.widget.setText("")
                elif hasattr(child_fw.widget, "clear"):
                    child_fw.widget.clear()
                set_child_visible(False, child_fw, p_btn)

            btn_add.clicked.connect(on_add_clicked)
            btn_remove.clicked.connect(on_remove_clicked)

            # Initial visibility based on current value
            has_val = bool(str(fw.value() or "").strip())
            set_child_visible(has_val, fw, btn_add)

        # Action buttons the module declared. Inside the scrolled content, so
        # Cancel and Save stay pinned at the bottom of the dialog whatever the
        # module declares.
        for action in describe_actions(module_inst, lang):
            btn = QPushButton(f"⚡ {action['label']}")
            btn.setStyleSheet(ACTION_BTN_STYLE)
            btn.clicked.connect(lambda _checked=False, a=action: self._invoke_action(a))
            form.addRow(btn)

        btn_box = QHBoxLayout()
        btn_box.addStretch()
        btn_cancel = QPushButton(self._tr("btn_cancel"))
        btn_cancel.setStyleSheet(SECONDARY_BTN_STYLE)
        btn_cancel.clicked.connect(self.reject)
        btn_ok = QPushButton(self._tr("btn_save"))
        btn_ok.setStyleSheet(PRIMARY_BTN_STYLE)
        btn_ok.clicked.connect(self.accept)
        btn_box.addWidget(btn_cancel)
        btn_box.addWidget(btn_ok)
        layout.addLayout(btn_box)

        self._track_changes(form)
        self.resize(self._preferred_width, self.sizeHint().height())

    # ------------------------------------------------------------------ sizing
    @staticmethod
    def _width_for(parent) -> int:
        """A little narrower than the window this dialog opens over, capped at DIALOG_MAX_WIDTH."""
        try:
            window = parent.window() if parent is not None else None
            if window is not None and window.width() > 0:
                width = max(DIALOG_MIN_WIDTH, window.width() - DIALOG_WIDTH_INSET)
                return min(DIALOG_MAX_WIDTH, width)
        except Exception:
            pass
        return min(DIALOG_MAX_WIDTH, DIALOG_FALLBACK_WIDTH)

    # --------------------------------------------------------- change tracking
    def _track_changes(self, form: QFormLayout) -> None:
        """
        Mark every field whose value stops matching what the dialog opened with.

        This exists because a value can change without being edited: a scroll
        wheel over a spin box or a combo moves it, and the dialog would
        otherwise save that silently. The label turns amber with a dot and the
        input gets an amber border; returning the value restores both, so the
        mark always means "differs from what you opened".
        """
        for fw in self.fields.values():
            try:
                baseline = fw.value()
            except Exception:
                continue
            label = form.labelForField(fw.container)
            self._connect_change_signals(fw, baseline, label)

    def _connect_change_signals(self, fw: "FieldWidget", baseline, label) -> None:
        widget = fw.widget
        original_label = label.text() if isinstance(label, QLabel) else ""
        original_style = widget.styleSheet()
        original_tooltip = label.toolTip() if isinstance(label, QLabel) else ""

        def refresh(*_args):
            try:
                changed = fw.value() != baseline
            except Exception:
                return
            if isinstance(label, QLabel):
                label.setStyleSheet(CHANGED_LABEL_STYLE if changed else "")
                label.setText((CHANGED_MARKER + original_label) if changed
                              else original_label)
                # The mark doubles as the undo control: the label is only
                # clickable while it means something, so an unchanged title
                # cannot look like a button that does nothing.
                label.setCursor(Qt.PointingHandCursor if changed
                                else Qt.ArrowCursor)
                label.setToolTip(
                    self._tr_fmt("tooltip_reset_field", value=baseline)
                    if changed else original_tooltip
                )
            # Appended rather than replaced: these widgets carry detailed
            # stylesheets and rebuilding one here would lose the arrow, the
            # padding and the popup styling.
            widget.setStyleSheet(
                f"{original_style}\n{type(widget).__name__} "
                f"{{ border: 1px solid {CHANGED_COLOR}; }}"
                if changed else original_style
            )

        for signal_name in ("valueChanged", "currentIndexChanged",
                            "editTextChanged", "textChanged", "toggled"):
            signal = getattr(widget, signal_name, None)
            if signal is None:
                continue
            try:
                signal.connect(refresh)
            except Exception:
                pass

        if isinstance(label, _FieldLabel):
            def on_label_clicked(_fw=fw, _base=baseline, _r=refresh):
                # Only while it differs - otherwise a click on any title would
                # look like it did something.
                try:
                    if _fw.value() == _base:
                        return
                except Exception:
                    return
                if _restore_value(_fw.widget, _base):
                    _r()

            label.clicked.connect(on_label_clicked)

    def _tr_fmt(self, key: str, **fmt) -> str:
        """Translate then format. A translator handed in by the frontend may not
        accept format arguments."""
        text = self._tr(key)
        if text == key:
            text = language.tr(key, **fmt)
            return text
        try:
            return text.format(**fmt)
        except Exception:
            return text

    # ------------------------------------------------------- widget construction
    def _section_rule(self, caption: str) -> QWidget:
        """
        A horizontal rule, optionally captioned, marking a new group of fields.

        Spans the whole form row rather than sitting in the field column, so the
        break reads as a division of the dialog instead of as another setting.
        """
        holder = QWidget()
        box = QVBoxLayout(holder)
        box.setContentsMargins(0, 8, 0, 2)
        box.setSpacing(3)
        if caption:
            title = QLabel(caption)
            title.setStyleSheet(
                "color: #cbd5e1; font-size: 11px; font-weight: bold;"
                "letter-spacing: 1px;"
            )
            box.addWidget(title)
        rule = QFrame()
        rule.setFrameShape(QFrame.HLine)
        rule.setFixedHeight(1)
        rule.setStyleSheet("background-color: #334155; border: none;")
        box.addWidget(rule)
        return holder

    def _style_combo(self, combo: QComboBox):
        combo.setStyleSheet(_combo_style(self._arrow))
        combo.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        if combo.isEditable():
            # An editable combo keeps its text in a child QLineEdit, and the
            # combo's own vertical padding shrinks that child's rect. Measured:
            # a 27 px combo left the line edit 13 px tall, which clipped the
            # text away entirely - the port field looked blank even with a
            # value in it. Give the child the height back and let it draw
            # through the combo's background.
            combo.setMinimumHeight(EDITABLE_COMBO_MIN_HEIGHT)
            line = combo.lineEdit()
            if line is not None:
                line.setStyleSheet(EDITABLE_COMBO_LINE_STYLE)
                line.setMinimumHeight(EDITABLE_COMBO_LINE_MIN_HEIGHT)
        if self._combo_popup_setup:
            try:
                self._combo_popup_setup(combo)
            except Exception:
                pass

    def _build_field(self, spec: FieldSpec) -> FieldWidget:
        if spec.type == "bool":
            box = QCheckBox(spec.label if spec.framework else "")
            box.setStyleSheet("color: white; font-weight: bold;")
            box.setChecked(bool(spec.value))
            return FieldWidget(spec, box, box.isChecked)

        if spec.type == "number":
            is_int = (spec.decimals == 0) or (
                spec.decimals is None and isinstance(spec.value, int) and not isinstance(spec.value, bool)
            )
            if is_int:
                box = QSpinBox()
                box.setRange(int(spec.minimum if spec.minimum is not None else -1_000_000),
                             int(spec.maximum if spec.maximum is not None else 1_000_000))
                box.setValue(int(spec.value or 0))
                if spec.step:
                    box.setSingleStep(int(spec.step))
                host = with_steppers(box)
                return FieldWidget(spec, box, box.value,
                                   container=host, enable_target=host)

            box = QDoubleSpinBox()
            box.setDecimals(int(spec.decimals) if spec.decimals is not None else 2)
            box.setRange(float(spec.minimum if spec.minimum is not None else -999999.0),
                         float(spec.maximum if spec.maximum is not None else 999999.0))
            box.setValue(float(spec.value or 0.0))
            if spec.step:
                box.setSingleStep(float(spec.step))
            host = with_steppers(box)
            return FieldWidget(spec, box, box.value,
                               container=host, enable_target=host)

        if spec.type == "enum":
            combo = QComboBox()
            combo.setEditable(bool(spec.editable))
            # option_labels lets the list read as names while the stored value
            # stays the one the protocol needs. The value rides along on the
            # item rather than being recovered from the text, so a label that
            # happens to look numeric cannot be parsed back into the wrong
            # value.
            labels = spec.option_labels if spec.option_labels else None
            for index, opt in enumerate(spec.options):
                combo.addItem(labels[index] if labels else str(opt), userData=opt)
            current = combo.findData(spec.value)
            if current >= 0:
                combo.setCurrentIndex(current)
            else:
                combo.setCurrentText(str(spec.value))
            self._style_combo(combo)
            original = {str(o): o for o in spec.options}

            def read_enum():
                index = combo.currentIndex()
                # An editable combo can hold text the user typed, which does not
                # correspond to the selected index - only trust the index when
                # its own text is what is showing.
                if index >= 0 and combo.itemText(index) == combo.currentText():
                    data = combo.itemData(index)
                    if data is not None:
                        return data
                text = combo.currentText().strip()
                if text in original:
                    return original[text]
                # Preserve the type when the user typed into an editable combo.
                for caster in (int, float):
                    try:
                        return caster(text)
                    except ValueError:
                        continue
                return text

            if getattr(spec, "rescan", False) and callable(getattr(spec, "rescan_fn", None)):
                row = QWidget()
                top = QHBoxLayout(row)
                top.setContentsMargins(0, 0, 0, 0)
                top.setSpacing(8)
                top.addWidget(combo)

                btn_refresh = QPushButton("🔄")
                btn_refresh.setToolTip(self._tr("tooltip_rescan_nrfutil"))
                btn_refresh.setFixedWidth(44)
                btn_refresh.setStyleSheet(SECONDARY_BTN_STYLE)

                def do_rescan():
                    current_txt = combo.currentText().strip()
                    try:
                        nrfutil_fw = self.fields.get("nrfutil_path")
                        nrfutil_val = nrfutil_fw.value() if nrfutil_fw else None
                        try:
                            new_opts = list(spec.rescan_fn(nrfutil_val) or [])
                        except TypeError:
                            new_opts = list(spec.rescan_fn() or [])
                    except Exception:
                        new_opts = []
                    combo.blockSignals(True)
                    combo.clear()
                    original.clear()
                    if current_txt and current_txt not in [str(o) for o in new_opts]:
                        combo.addItem(current_txt, userData=current_txt)
                        original[current_txt] = current_txt
                    for opt in new_opts:
                        combo.addItem(str(opt), userData=opt)
                        original[str(opt)] = opt

                    idx = combo.findData(current_txt)
                    if idx >= 0:
                        combo.setCurrentIndex(idx)
                    else:
                        combo.setCurrentText(current_txt)
                    combo.blockSignals(False)

                btn_refresh.clicked.connect(do_rescan)
                top.addWidget(btn_refresh)
                return FieldWidget(spec, combo, read_enum, container=row, enable_target=row)

            return FieldWidget(spec, combo, read_enum)

        if spec.type in ("port", "ppk_device"):
            # One widget for both device pickers. What differs is only where the
            # list comes from and what the "nothing found" text says, so the
            # rescan button, the stale-value warning and the editable line are
            # shared rather than duplicated.
            is_ppk = spec.type == "ppk_device"
            scan = (ppk_registry.available_devices if is_ppk
                    else serial_registry.available_ports)
            combo = QComboBox()
            combo.setEditable(True)
            self._style_combo(combo)

            # Only ports actually present go into the dropdown. A stored value
            # for a port that has since disappeared stays as the editable line
            # text but is left out of the list, so the value is not lost while
            # the fact that it is absent stays visible.
            warning = _wrapped_label("", WARNING_LABEL_STYLE)

            # Cache the last scan so typing does not re-enumerate ports.
            found_ports: List[str] = []
            # Whether a PPK2 is attached, scanned once. A serial port that is
            # configured but absent usually means one thing on this bench: the
            # PPK2 supplies the board, its output is off, and the board's serial
            # interface therefore does not exist to be found. Saying so turns a
            # dead end into an instruction.
            ppk_attached = False
            if not is_ppk:
                try:
                    ppk_attached = bool(ppk_registry.available_devices())
                except Exception:
                    ppk_attached = False

            def update_warning(text: str):
                stale = bool(text) and text not in found_ports
                if stale:
                    if is_ppk:
                        suffix = (language.tr("ppk_stale_found", count=len(found_ports))
                                  if found_ports else language.tr("ppk_stale_none"))
                        warning.setText(language.tr("ppk_stale_warning",
                                                    device=text, suffix=suffix))
                        tip = language.tr("ppk_stale_tooltip")
                        combo.setToolTip(tip)
                        warning.setToolTip(tip)
                    else:
                        suffix = (language.tr("port_stale_found", count=len(found_ports))
                                  if found_ports else language.tr("port_stale_none"))
                        message = language.tr("port_stale_warning", port=text,
                                              suffix=suffix)
                        if ppk_attached:
                            message += " " + language.tr("port_stale_ppk_hint")
                        warning.setText(message)
                        # The short line says what is wrong; the tooltip carries
                        # the full instruction, where length costs nothing.
                        tip = language.tr("port_stale_tooltip")
                        combo.setToolTip(tip)
                        warning.setToolTip(tip)
                else:
                    warning.setText("")
                    combo.setToolTip("")
                    warning.setToolTip("")
                warning.setVisible(stale)

            def rescan(keep: str):
                found_ports.clear()
                found_ports.extend(scan())
                combo.blockSignals(True)
                combo.clear()
                if not is_ppk:
                    combo.addItem("")
                combo.addItems(found_ports)
                # Auto-select when exactly one PPK2 is attached and nothing was chosen yet.
                if is_ppk and not keep and len(found_ports) == 1:
                    keep = found_ports[0]
                combo.setCurrentText(keep)
                combo.blockSignals(False)
                update_warning(keep)

            rescan(str(spec.value or ""))

            # The container is exactly one line: the picker and its rescan
            # button. The warning is handed back as its own form row instead of
            # being stacked in here - see FieldWidget.extra_rows.
            row = QWidget()
            top = QHBoxLayout(row)
            top.setContentsMargins(0, 0, 0, 0)
            top.setSpacing(8)
            top.addWidget(combo)

            btn_refresh = QPushButton("🔄")
            btn_refresh.setToolTip(language.tr(
                "tooltip_rescan_ppk" if is_ppk else "tooltip_rescan_ports"))
            btn_refresh.setFixedWidth(44)
            btn_refresh.setStyleSheet(SECONDARY_BTN_STYLE)
            btn_refresh.clicked.connect(lambda: rescan(combo.currentText().strip()))
            top.addWidget(btn_refresh)

            # Refresh the warning when typing or picking from the list too.
            combo.editTextChanged.connect(lambda text: update_warning(text.strip()))

            return FieldWidget(spec, combo, lambda: combo.currentText().strip(),
                               extra_rows=[warning],
                               container=row, enable_target=row)

        if spec.type in ("file", "dir"):
            edit = QLineEdit(str(spec.value or ""))
            edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            edit.setStyleSheet(f"QLineEdit {{ {INPUT_STYLE} }}")
            if spec.placeholder:
                edit.setPlaceholderText(spec.placeholder)

            row = QWidget()
            row_layout = QHBoxLayout(row)
            row_layout.setContentsMargins(0, 0, 0, 0)
            row_layout.setSpacing(8)
            row_layout.addWidget(edit)

            btn_browse = QPushButton(language.tr("btn_browse"))
            btn_browse.setStyleSheet(SECONDARY_BTN_STYLE)

            def browse():
                title = spec.dialog_title or language.tr("dlg_select_file", label=spec.label)
                if spec.type == "dir":
                    path = QFileDialog.getExistingDirectory(self, title, edit.text().strip())
                else:
                    path, _ = QFileDialog.getOpenFileName(self, title, "", _file_filter(spec))
                if path:
                    edit.setText(path)

            btn_browse.clicked.connect(browse)
            row_layout.addWidget(btn_browse)

            return FieldWidget(spec, edit, lambda: edit.text().strip(),
                               container=row, enable_target=row)

        # text (default)
        edit = QLineEdit(str(spec.value if spec.value is not None else ""))
        edit.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        edit.setStyleSheet(MONO_INPUT_STYLE if spec.monospace else f"QLineEdit {{ {INPUT_STYLE} }}")
        if spec.placeholder:
            edit.setPlaceholderText(spec.placeholder)
        return FieldWidget(spec, edit, lambda: edit.text().strip())

    # ---------------------------------------------------------- action dispatch
    def _invoke_action(self, action: Dict[str, Any]):
        if action.get("confirm"):
            reply = QMessageBox.question(self, action["label"], action["confirm"])
            if reply != QMessageBox.Yes:
                return

        method = getattr(self.module, action["method"], None)
        if not callable(method):
            QMessageBox.warning(self, action["label"],
                                language.tr("msg_action_unavailable", action=action["method"]))
            return

        try:
            outcome = method(self.get_criteria())
        except Exception as e:
            QMessageBox.warning(self, action["label"], f"❌ {type(e).__name__}: {e}")
            return

        # The module only reports {"ok": bool, "message": str}; how to present
        # it is the frontend's decision.
        if isinstance(outcome, dict) and "ok" in outcome:
            message = str(outcome.get("message", ""))
            if outcome["ok"]:
                QMessageBox.information(self, action["label"], f"✅ {message}")
            else:
                QMessageBox.warning(self, action["label"], f"❌ {message}")
        elif outcome is not None:
            QMessageBox.information(self, action["label"], str(outcome))

    def get_criteria(self) -> dict:
        result = {}
        for key, fw in self.fields.items():
            if getattr(fw.spec, "added_by", None) and not getattr(fw, "is_active", True):
                result[key] = ""
            else:
                result[key] = fw.value()
        return result
