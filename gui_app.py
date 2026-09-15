import colorsys
import html
import inspect
import os
import re
import tempfile
import sys
import json
import time
import datetime
import getpass
import socket
import random
from typing import Dict, Any, List

from PySide6.QtCore import Qt, QMimeData, QPoint, QSize, Signal, Slot, QThread, QEventLoop, QTimer
from PySide6.QtCore import QUrl
from PySide6.QtGui import (
    QActionGroup, QColor, QCursor, QDesktopServices, QDrag, QFont, QIcon,
    QPainter, QPalette, QPixmap, QTextCursor, QTextDocument, QTextOption,
)
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QListWidget, QListWidgetItem, QScrollArea,
    QFrame, QDialog, QFormLayout, QDoubleSpinBox, QSpinBox, QLineEdit,
    QComboBox, QListView, QTableWidget, QTableWidgetItem, QTextEdit, QTextBrowser, QCheckBox, QMessageBox,
    QHeaderView, QSplitter, QFileDialog, QSizePolicy, QProgressBar, QToolButton,
    QStyle, QStyledItemDelegate, QStyleOptionViewItem
)

import matplotlib
matplotlib.use("QtAgg")
matplotlib.rcParams['font.family'] = ['Arial', 'AppleGothic', 'sans-serif']
matplotlib.rcParams['axes.unicode_minus'] = False

from matplotlib.backends.backend_qtagg import FigureCanvasQTAgg as FigureCanvas
from matplotlib.figure import Figure

# Above this many x values a chart is treated as a waveform rather than a few
# summary points: markers come off and the time axis is clamped to the data.
DENSE_CHART_POINTS = 40

# Ensure project root is in sys.path
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CARDS_DIR = os.path.join(BASE_DIR, "cards")
MODULES_DIR = CARDS_DIR
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from cards.base_card import BaseCard, BaseTestModule
from cards.serial_session import (
    CRITERIA_KEY as SERIAL_CRITERIA_KEY,
    SerialSessionError,
)

# The UI-agnostic core, shared with the CLI (cli_runner.py).
from core.language import LanguageManager, LANG_DISPLAY_NAMES
from core.modes import AppMode, ModeController
from core.recipe import (
    RECIPE_FILE_SUFFIX,
    Recipe,
    import_recipe,
    recipe_from_cards,
)
from core.dependencies import (
    PPK_OWNER_REQUIREMENT,
    SERIAL_OWNER_REQUIREMENT,
    check_dependencies,
    find_dependents,
    find_serial_session_owner,
)
from core.registry import (
    discover_cards,
    get_card,
    discover_modules,
    get_module,
)
from core.runner import (
    instrument_scope,
    register_serial_target,
    run_card as core_run_card,
    run_module as core_run_module,
)
from core.sequence import SequenceContext, count_step_executions
from core.trace import TraceLog, YieldStats
from ui_qt.recipe_dialogs import (
    ImportReportDialog,
    PasscodeDialog,
    RecipeInfoDialog,
    ask_dut_serial,
)
from ui_qt.schema_form import SchemaCriteriaDialog
from ui_qt.spinbox import with_steppers
from ui_qt.sequence_thread import SequenceRunnerThread
from ui_qt.live_console import LiveConsoleWidget

LOADED_CARDS: Dict[str, BaseCard] = {}
LOADED_MODULES = LOADED_CARDS
LAYOUT_FILE = os.path.join(BASE_DIR, "dashboard_layout.json")
ARROW_SVG_PATH = os.path.join(BASE_DIR, "static", "down_arrow.svg").replace("\\", "/")
ARROW_GRAY_SVG_PATH = os.path.join(BASE_DIR, "static", "down_arrow_gray.svg").replace("\\", "/")

# Helper function for unified QComboBox popup styling without double border
def setup_combobox_popup(combo: QComboBox, border_color="#3b82f6", bg_color="#1e293b"):
    view = QListView(combo)
    view.setStyleSheet(f"""
        QListView {{
            background-color: {bg_color};
            color: #f8fafc;
            border: 1px solid {border_color};
            border-radius: 8px;
            outline: 0px;
            padding: 4px;
        }}
        QListView::item {{
            min-height: 26px;
            padding: 6px 12px;
            border-radius: 4px;
            color: #f8fafc;
        }}
        QListView::item:hover, QListView::item:selected {{
            background-color: #3b82f6;
            color: #ffffff;
        }}
    """)
    combo.setView(view)
    container = view.window()
    if container:
        container.setWindowFlags(Qt.Popup | Qt.FramelessWindowHint | Qt.NoDropShadowWindowHint)
        container.setAttribute(Qt.WA_TranslucentBackground)
LANGUAGE_NAMES = LANG_DISPLAY_NAMES

# The language manager moved to core.language (Qt-free, shared with the CLI and web).
language = LanguageManager.get_instance()


TOOLBAR_BTN_STYLE = """
    QToolButton {
        background-color: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid #3b82f6;
        padding: 7px 14px;
        border-radius: 8px;
        font-weight: bold;
    }
    QToolButton:hover {
        background-color: #2563eb;
        color: white;
        border-color: #60a5fa;
    }
    QToolButton:pressed {
        background-color: #1d4ed8;
        color: white;
        border-color: #93c5fd;
    }
    QToolButton:disabled {
        background-color: transparent;
        color: #475569;
        border-color: #334155;
    }
"""

SMALL_BTN_STYLE = """
    QPushButton {
        background-color: #334155;
        color: white;
        padding: 6px 8px;
        border-radius: 6px;
        border: 1px solid rgba(255, 255, 255, 0.1);
    }
    QPushButton:hover { background-color: #475569; }
"""

MODE_BTN_OPERATOR_STYLE = """
    QPushButton {
        background-color: rgba(251, 191, 36, 0.15);
        color: #fbbf24;
        border: 1px solid #fbbf24;
        padding: 6px 14px;
        border-radius: 6px;
        font-weight: bold;
    }
    QPushButton:hover { background-color: #d97706; color: white; }
"""

MODE_BTN_ENGINEER_STYLE = """
    QPushButton {
        background-color: rgba(59, 130, 246, 0.15);
        color: #60a5fa;
        border: 1px solid #3b82f6;
        padding: 6px 14px;
        border-radius: 6px;
        font-weight: bold;
    }
    QPushButton:hover { background-color: #2563eb; color: white; }
"""

# Trace log location. On each station this folder is that station's source of record.
TRACE_DIR_NAME = "trace"


def load_test_cards():
    """Card discovery is core.registry's job, under the same rules as the CLI."""
    global LOADED_CARDS
    LOADED_CARDS.clear()
    LOADED_CARDS.update(discover_cards(BASE_DIR))


load_test_modules = load_test_cards


# FontAwesome icon name -> emoji badge for Qt.
# This table is only a convenience fallback: a module declaring info["emoji"]
# overrides it, so a new module using an icon that is not listed can still set
# its badge without touching the core.
ICON_EMOJI_MAP = {
    "fa-battery": "🔋",
    "fa-bolt": "⚡",
    "fa-tower-broadcast": "📡",
    "fa-broadcast": "📡",
    "fa-wifi": "📶",
    "fa-stopwatch": "⏱️",
    "fa-clock": "⏱️",
    "fa-plug": "🔌",
    "fa-floppy-disk": "💾",
    "fa-download": "💾",
    "fa-microchip": "🧩",
    "fa-memory": "🧠",
    "fa-thermometer": "🌡️",
    "fa-gauge": "📊",
    "fa-chart": "📈",
    "fa-vial": "🧪",
    "fa-flask": "🧪",
    "fa-gear": "⚙️",
    "fa-wrench": "🔧",
    "fa-shield": "🛡️",
    "fa-radio": "📻",
    "fa-antenna": "📡",
}


def get_module_emoji(icon_str: str, module_inst: BaseTestModule = None) -> str:
    """Emoji declared by the module > icon-name mapping > default."""
    if module_inst is not None:
        declared = (getattr(module_inst, "info", {}) or {}).get("emoji")
        if declared:
            return str(declared)
    for key, emoji in ICON_EMOJI_MAP.items():
        if key in (icon_str or ""):
            return emoji
    return "🧪"


def make_criteria_dialog(module_inst: BaseTestModule, current_criteria: dict, parent=None):
    """
    Build the schema-driven settings dialog for a module.
    There is no per-module_id branching; every module takes the same path.
    """
    return SchemaCriteriaDialog(
        module_inst,
        current_criteria,
        arrow_svg_path=ARROW_GRAY_SVG_PATH,
        lang=language.current_lang,
        translator=language.tr,
        combo_popup_setup=lambda c: setup_combobox_popup(
            c, border_color="rgba(255, 255, 255, 0.2)", bg_color="#1e293b"
        ),
        parent=parent,
    )


# ==============================================================================
# 1. Drag & Drop Source Palette (Left Sidebar)
# ==============================================================================
# Palette item data. Qt.UserRole already carries the module_id.
PALETTE_NAME_ROLE = Qt.UserRole + 1
PALETTE_DESC_ROLE = Qt.UserRole + 2
PALETTE_COLOR_ROLE = Qt.UserRole + 3


def _relative_luminance(hex_color: str) -> float:
    """WCAG relative luminance of an #rrggbb colour."""
    raw = hex_color.lstrip("#")
    channels = [int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4)]
    linear = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
              for c in channels]
    return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]


def contrast_ratio(fg: str, bg: str) -> float:
    """WCAG contrast ratio between two #rrggbb colours."""
    a, b = _relative_luminance(fg), _relative_luminance(bg)
    return (max(a, b) + 0.05) / (min(a, b) + 0.05)


def legible_on(color: str, background: str, target: float = 4.5,
               ceiling: float = 0.86) -> str:
    """
    The same colour, lightened only as far as it takes to be readable.

    A module's own accent colour is the obvious thing to tint its palette title
    with - it is what the dashboard card already uses, so the two match. But
    most of those colours are chosen to sit against a dark card border, not to
    be read as text: measured against the palette item's #334155, six of eleven
    landed between 2.4 and 2.9, which is *less* legible than the plain white
    they replaced. Tinting them raw would have made the titles harder to see
    while looking like an improvement.

    Hue and saturation are kept so a module stays recognisably its own colour;
    only lightness moves, and it stops as soon as the contrast target is met.
    """
    try:
        raw = color.lstrip("#")
        if len(raw) != 6:
            return color
        r, g, b = (int(raw[i:i + 2], 16) / 255.0 for i in (0, 2, 4))
        hue, light, sat = colorsys.rgb_to_hls(r, g, b)
        current = f"#{raw}"
        while light < ceiling:
            if contrast_ratio(current, background) >= target:
                return current
            light = min(ceiling, light + 0.02)
            rr, gg, bb = colorsys.hls_to_rgb(hue, light, sat)
            current = "#%02x%02x%02x" % (round(rr * 255), round(gg * 255),
                                         round(bb * 255))
        return current
    except Exception:
        return color


class ModuleItemDelegate(QStyledItemDelegate):
    """
    Draws a palette row as a bold name over a plain description.

    A QListWidgetItem holds plain text, so the name could not be emphasised -
    every row read as one undifferentiated block and the module you were looking
    for did not stand out. The text is therefore laid out as a small rich-text
    document while the **box** is still drawn by the stylesheet: the background,
    the rounded dashed border and the hover state all keep coming from the
    QListWidget::item rules rather than being reimplemented here, so the two
    cannot drift apart.
    """

    NAME_COLOR = "#f8fafc"
    DESC_COLOR = "#cbd5e1"
    ITEM_BACKGROUND = "#334155"

    @staticmethod
    def _document(index, width: int) -> QTextDocument:
        name = index.data(PALETTE_NAME_ROLE) or ""
        desc = index.data(PALETTE_DESC_ROLE) or ""
        color = index.data(PALETTE_COLOR_ROLE) or ModuleItemDelegate.NAME_COLOR
        doc = QTextDocument()
        doc.setDocumentMargin(0)
        option = doc.defaultTextOption()
        option.setWrapMode(QTextOption.WrapAtWordBoundaryOrAnywhere)
        doc.setDefaultTextOption(option)
        doc.setHtml(
            f'<div style="color:{color};font-weight:bold;">'
            f'{html.escape(name)}</div>'
            f'<div style="color:{ModuleItemDelegate.DESC_COLOR};'
            f'margin-top:3px;">{html.escape(desc)}</div>'
        )
        if width > 0:
            doc.setTextWidth(width)
        return doc

    def paint(self, painter, option, index):
        opt = QStyleOptionViewItem(option)
        self.initStyleOption(opt, index)
        # Blank the text so the style paints only the box; the document is
        # drawn on top of it below.
        opt.text = ""
        widget = opt.widget
        style = widget.style() if widget is not None else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem, opt, painter, widget)

        rect = style.subElementRect(QStyle.SE_ItemViewItemText, opt, widget)
        if rect.width() <= 0:
            return
        doc = self._document(index, rect.width())
        doc.setDefaultFont(opt.font)
        painter.save()
        painter.translate(rect.topLeft())
        doc.drawContents(painter)
        painter.restore()

    def height_for_width(self, index, width: int, font) -> int:
        """The wrapped height of one row's text at a given width."""
        doc = self._document(index, width)
        doc.setDefaultFont(font)
        return int(doc.size().height())


class ModulePaletteWidget(QListWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setDragEnabled(True)
        self.setSelectionMode(QListWidget.SingleSelection)
        self.itemDoubleClicked.connect(self.on_item_double_clicked)

        # A module description is a full sentence. Left on one line it produced a
        # horizontal scrollbar and let the sidebar demand more width than the
        # window had. Wrapping keeps the palette inside whatever width it is given.
        self.setWordWrap(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setTextElideMode(Qt.ElideNone)
        self.setItemDelegate(ModuleItemDelegate(self))
        # Space between a module box and the scrollbar. Neither of the obvious
        # routes works: Qt ignores a horizontal margin on ::item, and extra
        # right padding on the view moves the scrollbar inwards with the
        # viewport instead of separating them - measured, the box still ended
        # one pixel from the scrollbar track and the padding appeared on the
        # far side of it. Narrowing the viewport is what actually inserts the
        # gap, leaving the scrollbar where it is.
        self.setViewportMargins(0, 0, self.SCROLLBAR_GAP, 0)
        self.setStyleSheet("""
            QListWidget {
                background-color: #1e293b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 10px;
                padding: 6px;
            }
            QListWidget::item {
                background-color: #334155;
                color: #f8fafc;
                border-radius: 8px;
                padding: 12px;
                margin-bottom: 8px;
                border: 1px dashed rgba(255, 255, 255, 0.2);
            }
            QListWidget::item:hover {
                background-color: #475569;
                border-color: #3b82f6;
            }
        """)

    # Stylesheet geometry the height calculation has to account for. Keep these
    # in step with the rules above. Only the item's own padding and border are
    # subtracted here: the view's padding already shrank the viewport, and
    # viewport().width() is measured after that.
    ITEM_PADDING = 12
    ITEM_BORDER = 1
    ITEM_MARGIN_BOTTOM = 8

    # Gap between a module box and the scrollbar, applied as a viewport margin.
    SCROLLBAR_GAP = 8

    def resizeEvent(self, event):
        super().resizeEvent(event)
        # Wrapped text needs a different height at every width.
        self.refresh_item_heights()

    def refresh_item_heights(self):
        """
        Give each item an explicit height that fits its wrapped text.

        Qt sizes a word-wrapped item from the delegate, which does not know
        about the padding the stylesheet adds; the row ended up too short and
        the last line was elided with an ellipsis. Measuring the text here and
        setting the hint makes every description fully visible.
        """
        available = self.viewport().width() - (
            2 * (self.ITEM_PADDING + self.ITEM_BORDER)
        )
        if available < 40:
            return

        delegate = self.itemDelegate()
        font = self.font()
        for row in range(self.count()):
            item = self.item(row)
            index = self.indexFromItem(item)
            height = delegate.height_for_width(index, available, font)
            item.setSizeHint(QSize(
                0,
                height + 2 * self.ITEM_PADDING + self.ITEM_MARGIN_BOTTOM,
            ))

    def on_item_double_clicked(self, item):
        if not item:
            return
        module_id = item.data(Qt.UserRole)
        main_win = self.window()
        if hasattr(main_win, "add_card_to_dashboard"):
            main_win.add_card_to_dashboard(module_id, insert_idx=-1)

    def startDrag(self, supportedActions):
        item = self.currentItem()
        if not item:
            return
        module_id = item.data(Qt.UserRole)
        mime_data = QMimeData()
        mime_data.setText(module_id)
        
        drag = QDrag(self)
        drag.setMimeData(mime_data)
        drag.exec(Qt.CopyAction)


# ==============================================================================
# 2. Strict 3-Line Card Widget (TestCardWidget & ConfigCardWidget)
# ==============================================================================
# 2. Module Execution Runner Thread (Background QThread)
# ==============================================================================
class ModuleRunnerThread(QThread):
    """
    A thin QThread wrapper around core.runner.run_module.

    The execution rules - shared session acquisition and injection, the
    pre-execution command, exception handling - live in the core. All this class
    does is relay them as Qt signals, so the CLI and the GUI behave identically
    and a future frontend cannot drift away from them.
    """

    finished_signal = Signal(dict)
    error_signal = Signal(str)
    log_signal = Signal(str)

    def __init__(self, module: BaseTestModule, criteria: dict, use_mock: bool, parent=None):
        super().__init__(parent)
        self.setObjectName(f"ModuleRunnerThread-{getattr(module, 'module_id', 'unknown')}")
        self.module = module
        self.criteria = dict(criteria)
        self.use_mock = use_mock
        self.is_cancelled = False

    def cancel(self):
        self.is_cancelled = True

    def run(self):
        # Inside a batch run this nests, so the sessions are not closed when a
        # card finishes. For a standalone run everything closes with this card -
        # including the PPK2, which powers the DUT down and frees its port.
        with instrument_scope():
            try:
                outcome = core_run_module(
                    self.module,
                    self.criteria,
                    use_mock=self.use_mock,
                    log_callback=self.log_signal.emit,
                    is_cancelled=lambda: self.is_cancelled,
                )
            except Exception as e:
                self.error_signal.emit(f"{type(e).__name__}: {e}")
                return

            if isinstance(outcome.error, SerialSessionError):
                # Failing to acquire a resource is a configuration error, not a
                # verdict, so it is reported as an error.
                self.error_signal.emit(str(outcome.error))
                return

            self.finished_signal.emit(outcome.result)


class TestCardWidget(QFrame):
    card_clicked = Signal(str) # Emits card_id when clicked

    # Driven by the mode policy: False in operator mode or with a locked recipe.
    # The decision lives in core.modes; this only disables widgets.
    editable = True
    removable = True
    reorderable = True

    # Step-repeat progress display (0 means do not show it)
    iteration = 0
    iterations_total = 0

    # Why a prerequisite is unmet (empty string means all good)
    dependency_warning = ""

    
    def __init__(self, card_id: str, card_inst: BaseCard, initial_criteria: dict = None, parent=None):
        super().__init__(parent)
        self.card_id = card_id
        self.card = card_inst
        self.module = card_inst  # backward compatibility alias
        self.card_type = getattr(card_inst, "card_type", getattr(card_inst, "module_type", "test"))
        self.module_type = self.card_type
        self.drag_start_pos = QPoint()
        self.runner_thread = None
        self.start_run_time = None
        
        self.elapsed_timer = QTimer(self)
        self.elapsed_timer.setInterval(100)
        self.elapsed_timer.timeout.connect(self._on_elapsed_timer_tick)
        
        base_criteria = {k: v["value"] for k, v in card_inst.default_criteria.items()}
        if initial_criteria and isinstance(initial_criteria, dict):
            base_criteria.update(initial_criteria)
        self.criteria = base_criteria

        self.status = "PENDING"  # 'PENDING', 'RUNNING', 'PASS', 'FAIL'
        self.execution_time = "--"
        self.summary_text = language.tr("status_pending")
        self.last_result = None

        self.init_ui()

    def _on_elapsed_timer_tick(self):
        if self.status == "RUNNING" and self.start_run_time is not None:
            elapsed = time.time() - self.start_run_time
            self.execution_time = f"{elapsed:.1f}s"
            self.update_style()

    def init_ui(self):
        self.setObjectName("TestCard")
        self.setCursor(Qt.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        # Tight top/bottom: the three lines each carry their own padding, so the
        # box margin is added on top of that and was the largest single
        # contributor to the card's height.
        layout.setContentsMargins(16, 8, 16, 8)
        layout.setSpacing(5)

        # Top Action Bar
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(0, 0, 0, 0)
        
        # Line 1: Representative Test Icon + Name
        emoji = get_module_emoji(self.module.icon, self.module)
        mod_name = self.module.get_localized("name", language.current_lang)
        self.line1_label = QLabel(f"{emoji}  {mod_name}")
        self.line1_label.setStyleSheet("font-size: 15px; font-weight: bold; color: #f8fafc;")
        
        buttons_to_style = []

        if self.module_type == "test":
            self.btn_status = QPushButton("⏳")
            self.btn_status.setObjectName("btn_status")
            self.btn_status.setFixedSize(28, 28)
            self.btn_status.setCursor(Qt.PointingHandCursor)
            self.btn_status.setToolTip(language.tr("status_pending"))
            self.btn_status.clicked.connect(self.on_status_clicked)
            buttons_to_style.append(self.btn_status)

        self.btn_help = QPushButton("❓")
        self.btn_help.setFixedSize(28, 28)
        self.btn_help.setToolTip(language.tr("tooltip_help"))
        self.btn_help.clicked.connect(self.on_help_clicked)

        self.btn_settings = QPushButton("⚙️")
        self.btn_settings.setFixedSize(28, 28)
        self.btn_settings.setToolTip(language.tr("tooltip_settings"))
        self.btn_settings.clicked.connect(self.on_settings_clicked)

        self.btn_run = QPushButton("▶")
        self.btn_run.setFixedSize(28, 28)
        self.btn_run.setToolTip(language.tr("tooltip_run"))
        self.btn_run.clicked.connect(self.on_run_clicked)

        self.btn_delete = QPushButton("🗑")
        self.btn_delete.setFixedSize(28, 28)
        self.btn_delete.setToolTip(language.tr("tooltip_delete"))
        self.btn_delete.clicked.connect(self.on_delete_clicked)

        buttons_to_style.append(self.btn_help)
        buttons_to_style.append(self.btn_settings)
        if self.module_type == "test":
            buttons_to_style.append(self.btn_run)
        buttons_to_style.append(self.btn_delete)

        for btn in buttons_to_style:
            if btn is getattr(self, "btn_status", None):
                continue
            btn.setStyleSheet("""
                QPushButton {
                    background-color: rgba(255, 255, 255, 0.1);
                    border: 1px solid rgba(255, 255, 255, 0.15);
                    border-radius: 14px;
                    color: white;
                }
                QPushButton:hover {
                    background-color: #3b82f6;
                    border-color: #60a5fa;
                }
                QPushButton:pressed {
                    background-color: #1d4ed8;
                    border-color: #93c5fd;
                    padding-top: 2px;
                    padding-left: 2px;
                }
            """)

        top_bar.addWidget(self.line1_label)

        self.version_badge = QLabel(f"v{getattr(self.module, 'version', '1.0.0')}")
        self.version_badge.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.08);
            color: #94a3b8;
            font-size: 11px;
            font-weight: bold;
            font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
            padding: 2px 6px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.12);
        """)
        top_bar.addWidget(self.version_badge)

        self.wip_badge = QLabel()
        self.wip_badge.setVisible(False)
        top_bar.addWidget(self.wip_badge)

        top_bar.addStretch()
        for btn in buttons_to_style:
            top_bar.addWidget(btn)
        layout.addLayout(top_bar)

        # Line 2: Execution Time / Configuration info
        self.line2_label = QLabel()
        self.line2_label.setStyleSheet("""
            background-color: rgba(0, 0, 0, 0.25);
            color: #94a3b8;
            font-size: 12px;
            padding: 3px 8px;
            border-radius: 6px;
        """)
        layout.addWidget(self.line2_label)

        self.line2_label.setWordWrap(True)
        self.line2_label.setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Minimum)
        self.line2_label.setMinimumWidth(0)

        self.update_style()

    def update_style(self):
        emoji = get_module_emoji(self.module.icon, self.module)
        mod_name = self.module.get_localized("name", language.current_lang)
        self.line1_label.setText(f"{emoji}  {mod_name}")

        main_win = self.window()
        use_mock = main_win.mock_checkbox.isChecked() if hasattr(main_win, "mock_checkbox") else False
        is_ready = getattr(self.module, "is_ready", True)
        is_available = getattr(self.module, "is_available", lambda m=False: True)(use_mock)
        ver_text = f"v{getattr(self.module, 'version', '1.0.0')}"

        if hasattr(self, "version_badge"):
            self.version_badge.setText(ver_text)
            self.version_badge.setToolTip(f"{language.tr('lbl_version')}: {ver_text}")

        if hasattr(self, "wip_badge"):
            if not is_ready:
                self.wip_badge.setVisible(True)
                if use_mock:
                    self.wip_badge.setText(language.tr("badge_wip_mock_active"))
                    self.wip_badge.setStyleSheet("""
                        font-size: 11px; font-weight: bold; color: #fbbf24;
                        background-color: rgba(245, 158, 11, 0.18);
                        padding: 2px 6px; border-radius: 4px;
                        border: 1px solid rgba(245, 158, 11, 0.35);
                    """)
                    self.wip_badge.setToolTip(language.tr("tooltip_wip_mock_active"))
                else:
                    self.wip_badge.setText(language.tr("badge_wip_disabled"))
                    self.wip_badge.setStyleSheet("""
                        font-size: 11px; font-weight: bold; color: #f87171;
                        background-color: rgba(239, 68, 68, 0.18);
                        padding: 2px 6px; border-radius: 4px;
                        border: 1px solid rgba(239, 68, 68, 0.35);
                    """)
                    self.wip_badge.setToolTip(language.tr("tooltip_wip_card_disabled"))
            else:
                self.wip_badge.setVisible(False)

        self.btn_help.setToolTip(language.tr("tooltip_help"))
        self.btn_settings.setToolTip(language.tr("tooltip_settings"))
        self.btn_delete.setToolTip(language.tr("tooltip_delete"))
        if self.module_type == "test":
            if self.status == "RUNNING":
                self.btn_run.setText("⏹")
                self.btn_run.setEnabled(True)
                self.btn_run.setToolTip(language.tr("tooltip_stop"))
                self.btn_run.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(239, 68, 68, 0.2);
                        border: 1.5px solid #ef4444;
                        border-radius: 14px;
                        color: #ef4444;
                        font-size: 13px;
                    }
                    QPushButton:hover {
                        background-color: #ef4444;
                        color: white;
                        border-color: #f87171;
                    }
                    QPushButton:pressed {
                        background-color: #dc2626;
                        border-color: #fca5a5;
                        padding-top: 2px;
                        padding-left: 2px;
                    }
                """)
            else:
                self.btn_run.setText("▶")
                self.btn_run.setStyleSheet("""
                    QPushButton {
                        background-color: rgba(255, 255, 255, 0.1);
                        border: 1px solid rgba(255, 255, 255, 0.15);
                        border-radius: 14px;
                        color: white;
                        font-size: 13px;
                    }
                    QPushButton:hover {
                        background-color: #3b82f6;
                        border-color: #60a5fa;
                    }
                    QPushButton:pressed {
                        background-color: #1d4ed8;
                        border-color: #93c5fd;
                        padding-top: 2px;
                        padding-left: 2px;
                    }
                """)
                if is_available:
                    self.btn_run.setEnabled(True)
                    self.btn_run.setToolTip(language.tr("tooltip_run"))
                else:
                    self.btn_run.setEnabled(False)
                    self.btn_run.setToolTip(language.tr("tooltip_wip_card_disabled"))

        if self.module_type == "config":
            if getattr(self.module, "needs_ppk", False):
                device = str(self.criteria.get("ppk_port", "") or "").strip()
                mode = self.criteria.get("ppk_mode", "source_meter")
                mv = self.criteria.get("ppk_source_mv", 3000)
                power_on = bool(self.criteria.get("ppk_power_on", True))
                pwr_text = language.tr("card_ppk_power_on" if power_on else "card_ppk_power_off")
                dev_text = device or language.tr("card_ppk_no_device")
                self.line2_label.setText(
                    f"{language.tr('lbl_ppk_device')}: {dev_text}  |  {mode}  |  {mv} mV  |  {pwr_text}"
                )
            else:
                port = self.criteria.get("port", "COM1")
                baud = self.criteria.get("baudrate", 115200)
                flow = self.criteria.get("flowcontrol", "Hardware (RTS/CTS)")
                parity = self.criteria.get("parity", "None (N)")
                self.line2_label.setText(
                    f"{language.tr('lbl_port')}: {port}  |  {baud} bps  |  {parity}  |  Flow: {flow}"
                )
            
            self.setStyleSheet("""
                QFrame#TestCard {
                    background-color: rgba(30, 41, 59, 0.9);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-left: 6px solid #3b82f6;
                    border-radius: 12px;
                }
                QFrame#TestCard:hover {
                    background-color: rgba(51, 65, 85, 0.95);
                }
            """)
        else:
            summary = ""
            if hasattr(self.module, "get_criteria_summary"):
                try:
                    summary = self.module.get_criteria_summary(self.criteria)
                except Exception:
                    summary = ""

            if not is_available:
                summary_part = f"  |  ⚙️ {summary}" if summary else ""
                self.line2_label.setText(
                    f"{language.tr('lbl_status')}: {language.tr('status_wip_label')} · {ver_text}{summary_part}"
                )
                if hasattr(self, "btn_status"):
                    self.btn_status.setText("🚧")
                    self.btn_status.setToolTip(language.tr("status_wip_disabled"))
                    self.btn_status.setStyleSheet("""
                        QPushButton#btn_status {
                            background-color: rgba(100, 116, 139, 0.15);
                            border: 1.5px dashed rgba(148, 163, 184, 0.4);
                            border-radius: 14px;
                            font-size: 13px;
                            color: #94a3b8;
                        }
                        QPushButton#btn_status:hover {
                            background-color: rgba(100, 116, 139, 0.3);
                        }
                    """)
                self.setStyleSheet("""
                    QFrame#TestCard {
                        background-color: rgba(20, 25, 35, 0.65);
                        border: 1px dashed rgba(148, 163, 184, 0.25);
                        border-left: 6px solid #64748b;
                        border-radius: 12px;
                    }
                    QFrame#TestCard:hover {
                        background-color: rgba(30, 41, 59, 0.75);
                    }
                """)
                return

            req_serial = getattr(self.module, "needs_serial", False)
            if req_serial:
                override = str(self.criteria.get("port", "") or "").strip()
                if override:
                    serial_tag = language.tr("badge_serial_override", port=override)
                else:
                    from cards.serial_session import serial_registry
                    target_port = (serial_registry.default_target or {}).get("port", "")
                    short_port = os.path.basename(target_port) if target_port else ""
                    serial_tag = f"🔌 DUT ({short_port})" if short_port else language.tr("badge_serial_req")
            else:
                serial_tag = language.tr("badge_no_serial")

            repeat_tag = ""
            if self.iterations_total > 1:
                repeat_tag = "  |  " + language.tr(
                    "badge_step_iteration", current=self.iteration, total=self.iterations_total
                )
            else:
                configured = self.repeat_count()
                if configured > 1:
                    repeat_tag = "  |  " + language.tr("badge_step_repeat", count=configured)

            dep_tag = "  |  " + language.tr("dep_badge_unmet") if self.dependency_warning else ""

            parts = []
            if self.status in ("PENDING", "READY"):
                if summary:
                    parts.append(f"⚙️ {summary}")
                else:
                    parts.append(f"{language.tr('lbl_time')}: {self.execution_time}")
            else:
                parts.append(f"{language.tr('lbl_time')}: {self.execution_time}")
                if summary:
                    parts.append(f"⚙️ {summary}")

            parts.append(serial_tag)
            if repeat_tag:
                parts.append(repeat_tag.strip(" | "))
            if dep_tag:
                parts.append(dep_tag.strip(" | "))

            self.line2_label.setText("  |  ".join(filter(None, parts)))
            border_color = "#f59e0b"
            
            if hasattr(self, "btn_status"):
                if self.status == "PASS":
                    border_color = "#10b981"
                    tip = f"PASS: {self.summary_text}" if self.summary_text else "PASS"
                    self.btn_status.setText("✅")
                    self.btn_status.setToolTip(tip)
                    self.btn_status.setStyleSheet("""
                        QPushButton#btn_status {
                            background-color: rgba(16, 185, 129, 0.2);
                            border: 1.5px solid #10b981;
                            border-radius: 14px;
                            font-size: 13px;
                            color: #10b981;
                        }
                        QPushButton#btn_status:hover {
                            background-color: rgba(16, 185, 129, 0.35);
                            border-color: #34d399;
                        }
                    """)
                elif self.status == "FAIL":
                    border_color = "#ef4444"
                    tip = f"FAIL: {self.summary_text}" if self.summary_text else "FAIL"
                    self.btn_status.setText("❌")
                    self.btn_status.setToolTip(tip)
                    self.btn_status.setStyleSheet("""
                        QPushButton#btn_status {
                            background-color: rgba(239, 68, 68, 0.2);
                            border: 1.5px solid #ef4444;
                            border-radius: 14px;
                            font-size: 13px;
                            color: #ef4444;
                        }
                        QPushButton#btn_status:hover {
                            background-color: rgba(239, 68, 68, 0.35);
                            border-color: #f87171;
                        }
                    """)
                elif self.status == "RUNNING":
                    border_color = "#3b82f6"
                    self.btn_status.setText("⏳")
                    self.btn_status.setToolTip(language.tr("status_running"))
                    self.btn_status.setStyleSheet("""
                        QPushButton#btn_status {
                            background-color: rgba(59, 130, 246, 0.2);
                            border: 1.5px solid #3b82f6;
                            border-radius: 14px;
                            font-size: 13px;
                            color: #60a5fa;
                        }
                        QPushButton#btn_status:hover {
                            background-color: rgba(59, 130, 246, 0.35);
                            border-color: #93c5fd;
                        }
                    """)
                else:
                    self.summary_text = language.tr("status_pending")
                    self.btn_status.setText("⏳")
                    self.btn_status.setToolTip(self.summary_text)
                    self.btn_status.setStyleSheet("""
                        QPushButton#btn_status {
                            background-color: rgba(245, 158, 11, 0.15);
                            border: 1.5px solid rgba(245, 158, 11, 0.45);
                            border-radius: 14px;
                            font-size: 13px;
                            color: #fbbf24;
                        }
                        QPushButton#btn_status:hover {
                            background-color: rgba(245, 158, 11, 0.25);
                            border-color: #f59e0b;
                        }
                    """)

            self.setStyleSheet(f"""
                QFrame#TestCard {{
                    background-color: rgba(30, 41, 59, 0.85);
                    border: 1px solid rgba(255, 255, 255, 0.1);
                    border-left: 6px solid {border_color};
                    border-radius: 12px;
                }}
                QFrame#TestCard:hover {{
                    background-color: rgba(51, 65, 85, 0.95);
                }}
            """)

    def set_dependency_warning(self, reason: str):
        """Show a warning on the card when a prerequisite is missing."""
        if self.dependency_warning == reason:
            return
        self.dependency_warning = reason
        self.setToolTip(language.tr("dep_tooltip_unmet", reasons=reason) if reason else "")
        self.update_style()

    def set_iteration(self, iteration: int, iterations_total: int):
        """Show step-repeat progress on the card, e.g. 3/5."""
        self.iteration = iteration
        self.iterations_total = iterations_total
        self.update_style()

    def repeat_count(self) -> int:
        """The step-repeat count configured on this card."""
        try:
            return max(1, int(self.criteria.get("step_repeat", 1)))
        except (TypeError, ValueError):
            return 1

    def set_editable(self, editable: bool, removable: bool = True, reorderable: bool = True):
        """Apply the mode policy to this card."""
        self.editable = editable
        self.removable = removable
        self.reorderable = reorderable

        self.btn_settings.setEnabled(editable)
        self.btn_settings.setToolTip(
            language.tr("tooltip_settings") if editable else language.tr("tooltip_settings_locked")
        )
        self.btn_delete.setVisible(removable)

    def mousePressEvent(self, event):
        if not self.reorderable:
            # Cards cannot be dragged to reorder them in operator mode.
            super().mousePressEvent(event)
            return
        if event.button() == Qt.LeftButton:
            event_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            self.drag_start_pos = event_pos
            self.is_drag_active = False
            self.setCursor(Qt.ClosedHandCursor)
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if not (event.buttons() & Qt.LeftButton):
            return
            
        event_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        if (event_pos - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
            return
        
        if self.module.module_type == "config":
            return

        self.is_drag_active = True

        drag = QDrag(self)
        mime_data = QMimeData()
        mime_data.setData("application/x-dashboard-card-id", self.card_id.encode("utf-8"))
        drag.setMimeData(mime_data)
        
        pixmap = self.grab()
        drag.setPixmap(pixmap)
        drag.setHotSpot(event_pos)

        drag.exec(Qt.MoveAction)
        self.setCursor(Qt.PointingHandCursor)

    def mouseReleaseEvent(self, event):
        self.setCursor(Qt.PointingHandCursor)
        if event.button() == Qt.LeftButton:
            event_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
            if not getattr(self, "is_drag_active", False) and (event_pos - self.drag_start_pos).manhattanLength() < QApplication.startDragDistance():
                child = self.childAt(event_pos)
                if not child or not isinstance(child, QPushButton):
                    self.card_clicked.emit(self.card_id)
        super().mouseReleaseEvent(event)

    def on_move_up_clicked(self):
        main_win = self.window()
        if hasattr(main_win, "move_card"):
            main_win.move_card(self.card_id, delta=-1)

    def on_move_down_clicked(self):
        main_win = self.window()
        if hasattr(main_win, "move_card"):
            main_win.move_card(self.card_id, delta=1)

    def on_status_clicked(self):
        dialog = DetailDialog(self, self)
        dialog.exec()

    def on_help_clicked(self):
        dialog = ModuleHelpDialog(self.module, self)
        dialog.exec()

    def on_settings_clicked(self):
        if not self.editable:
            QMessageBox.information(
                self, language.tr("msg_operator_mode_title"), language.tr("msg_criteria_locked")
            )
            return
        # No module_id branching: the form is built purely from the type,
        # options and actions the module declares, so adding a module never
        # requires editing this code.
        dialog = make_criteria_dialog(self.module, self.criteria, parent=self)
        if dialog.exec() == QDialog.Accepted:
            self.criteria = dialog.get_criteria()
            self.update_style()
            main_win = self.window()
            # Setting a port override satisfies the serial prerequisite, so the
            # badge has to be re-evaluated here; without this the card keeps
            # warning about a dependency it no longer has.
            if hasattr(main_win, "refresh_dependency_badges"):
                main_win.refresh_dependency_badges()
            if hasattr(main_win, "save_dashboard_state"):
                main_win.save_dashboard_state()

    @Slot(dict)
    def handle_thread_finished(self, res: dict):
        if self.elapsed_timer.isActive():
            self.elapsed_timer.stop()
        self.status = res.get("result", "FAIL")
        exec_sec = res.get("execution_time_sec", 0.0)
        self.execution_time = f"{exec_sec:.2f}s"
        self.summary_text = res.get("summary_text", "")
        self.last_result = res
        self.update_style()
        if hasattr(self, "_active_finished_callback") and self._active_finished_callback:
            cb = self._active_finished_callback
            self._active_finished_callback = None
            cb(res)

    @Slot(str)
    def handle_thread_error(self, err_str: str):
        if self.elapsed_timer.isActive():
            self.elapsed_timer.stop()
        self.status = "FAIL"
        self.execution_time = "0.00s"
        self.summary_text = f"Error: {err_str}"
        self.last_result = None
        self.update_style()
        if hasattr(self, "_active_finished_callback") and self._active_finished_callback:
            cb = self._active_finished_callback
            self._active_finished_callback = None
            cb({"result": "FAIL", "summary_text": self.summary_text})

    def stop_test(self):
        """Stop the currently running test on this card."""
        if self.runner_thread and self.runner_thread.isRunning():
            self.runner_thread.cancel()
            self.runner_thread.quit()
            if not self.runner_thread.wait(1000):
                self.runner_thread.terminate()
                self.runner_thread.wait(300)
        self.status = "FAIL"
        self.execution_time = "0.00s"
        self.summary_text = language.tr("status_stopped")
        self.last_result = None
        if self.elapsed_timer.isActive():
            self.elapsed_timer.stop()
        self.update_style()
        if hasattr(self, "_active_finished_callback") and self._active_finished_callback:
            cb = self._active_finished_callback
            self._active_finished_callback = None
            cb({"result": "FAIL", "summary_text": self.summary_text})

    def on_run_clicked(self, on_finished_callback=None):
        if self.status == "RUNNING":
            main_win = self.window()
            if hasattr(main_win, "is_running_all") and main_win.is_running_all:
                main_win.request_stop()
            else:
                self.stop_test()
            return

        main_win = self.window()
        # Falling back to True here would silently simulate when the attribute is
        # missing, which is exactly the failure mode to avoid.
        use_mock = main_win.mock_checkbox.isChecked() if hasattr(main_win, "mock_checkbox") else False

        if not getattr(self.module, "is_ready", True) and not use_mock:
            QMessageBox.warning(
                self,
                language.tr("title_card_wip"),
                language.tr("msg_card_wip_run_blocked", name=self.module.get_localized("name", language.current_lang)),
            )
            return
        
        exec_criteria = dict(self.criteria)
        # Register the sequence default from the top toolbar so steps without
        # an override inherit it via core.runner.acquire_serial_session.
        if hasattr(main_win, "_register_serial_target"):
            main_win._register_serial_target()

        self.status = "RUNNING"
        self.start_run_time = time.time()
        self.execution_time = "0.0s"
        self.update_style()

        if self.runner_thread and self.runner_thread.isRunning():
            self.runner_thread.cancel()
            self.runner_thread.quit()
            if not self.runner_thread.wait(1500):
                self.runner_thread.terminate()
                self.runner_thread.wait(500)

        self.live_logs = []
        self._active_finished_callback = on_finished_callback
        self.runner_thread = ModuleRunnerThread(self.module, exec_criteria, use_mock, self)
        self.runner_thread.finished_signal.connect(self.handle_thread_finished)
        self.runner_thread.error_signal.connect(self.handle_thread_error)
        self.runner_thread.log_signal.connect(lambda line: self.live_logs.append(line))

        main_win = self.window()
        if hasattr(main_win, "live_console"):
            self.runner_thread.log_signal.connect(
                lambda line, cid=self.card_id: main_win.live_console.append_log(line, step_id=cid)
            )

        self.elapsed_timer.start()
        self.runner_thread.start()

    def on_delete_clicked(self):
        if not self.removable:
            return
        main_win = self.window()
        if hasattr(main_win, "remove_card"):
            main_win.remove_card(self.card_id)


# ==============================================================================
# 3. Dashboard Drop Zone Widget (Center Area)
# ==============================================================================
class DashboardDropZone(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAcceptDrops(True)
        
        self.main_layout = QVBoxLayout(self)
        self.main_layout.setContentsMargins(16, 16, 16, 16)
        self.main_layout.setSpacing(12)
        self.main_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.placeholder = QLabel(language.tr("placeholder_drop"))
        self.placeholder.setAlignment(Qt.AlignCenter)
        self.placeholder.setStyleSheet("""
            QLabel {
                border: 2px dashed rgba(255, 255, 255, 0.2);
                border-radius: 16px;
                color: #94a3b8;
                font-size: 15px;
                padding: 60px;
                background-color: rgba(15, 23, 42, 0.3);
            }
        """)
        self.main_layout.addWidget(self.placeholder)
        self.cards: Dict[str, TestCardWidget] = {}

    def update_placeholder_text(self):
        self.placeholder.setText(language.tr("placeholder_drop"))

    def rebuild_layout(self):
        """Cleanly re-synchronizes all card widgets in main_layout according to self.cards."""
        while self.main_layout.count() > 0:
            self.main_layout.takeAt(0)

        if len(self.cards) == 0:
            self.main_layout.addWidget(self.placeholder)
            self.placeholder.show()
        else:
            self.placeholder.hide()
            for card_w in self.cards.values():
                self.main_layout.addWidget(card_w)

    def get_drop_target_index(self, pos: QPoint) -> int:
        y = pos.y()
        card_widgets = list(self.cards.values())
        for idx, widget in enumerate(card_widgets):
            widget_center_y = widget.geometry().center().y()
            if y < widget_center_y:
                return idx
        return len(card_widgets)

    def dragEnterEvent(self, event):
        if event.mimeData().hasText() or event.mimeData().hasFormat("application/x-dashboard-card-id"):
            event.acceptProposedAction()
            self.setStyleSheet("background-color: rgba(59, 130, 246, 0.08); border-radius: 12px;")

    def dragMoveEvent(self, event):
        if event.mimeData().hasText() or event.mimeData().hasFormat("application/x-dashboard-card-id"):
            event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self.setStyleSheet("")

    def dropEvent(self, event):
        self.setStyleSheet("")
        main_win = self.window()
        event_pos = event.position().toPoint() if hasattr(event, "position") else event.pos()
        target_idx = self.get_drop_target_index(event_pos)

        # Config cards occupy the top of the list - one per shared instrument, so
        # possibly more than one. A test card may not be dropped inside that block.
        config_count = sum(1 for c in self.cards.values()
                           if c.module.module_type == "config")

        if event.mimeData().hasFormat("application/x-dashboard-card-id"):
            card_id = bytes(event.mimeData().data("application/x-dashboard-card-id")).decode("utf-8")
            card_w = self.cards.get(card_id)
            # Decided by module_type, not module_id.
            if card_w and card_w.module.module_type != "config":
                target_idx = max(config_count, target_idx)
            if hasattr(main_win, "reorder_card_to_index"):
                event.acceptProposedAction()
                main_win.reorder_card_to_index(card_id, target_idx)
            return

        if event.mimeData().hasText():
            module_id = event.mimeData().text()
            dropped = get_module(LOADED_MODULES, module_id)
            if dropped is not None:
                if dropped.module_type != "config":
                    target_idx = max(config_count, target_idx)
                event.acceptProposedAction()
                if hasattr(main_win, "add_card_to_dashboard"):
                    main_win.add_card_to_dashboard(module_id, insert_idx=target_idx)

    def add_card(self, card_widget: TestCardWidget, insert_idx: int = -1):
        if insert_idx < 0 or insert_idx >= len(self.cards):
            self.cards[card_widget.card_id] = card_widget
        else:
            card_items = list(self.cards.items())
            card_items.insert(insert_idx, (card_widget.card_id, card_widget))
            self.cards = dict(card_items)
        self.rebuild_layout()

    def remove_card(self, card_id: str):
        if card_id in self.cards:
            widget = self.cards.pop(card_id)
            self.rebuild_layout()
            if hasattr(widget, "runner_thread") and widget.runner_thread and widget.runner_thread.isRunning():
                try:
                    widget.runner_thread.cancel()
                    widget.runner_thread.quit()
                    widget.runner_thread.wait(1000)
                except Exception:
                    pass
            widget.deleteLater()



# ==============================================================================
# 4. Module Help Dialog (ModuleHelpDialog)
# ==============================================================================
class ModuleHelpDialog(QDialog):
    def __init__(self, module_inst: BaseTestModule, parent=None):
        super().__init__(parent)
        self.module = module_inst
        mod_name = module_inst.get_localized("name", language.current_lang)
        mod_desc = module_inst.get_localized("description", language.current_lang)
        help_text = module_inst.get_localized("help_text", language.current_lang)

        self.setWindowTitle(f"❓ {mod_name} - {language.tr('tooltip_help')}")
        self.resize(880, 720)
        self.setMinimumSize(640, 500)
        self.setStyleSheet("background-color: #0f172a; color: #f8fafc;")

        self._updating_image = False
        self.orig_pixmap = None
        self.img_lbl = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 22, 22, 22)
        layout.setSpacing(14)

        header = QHBoxLayout()
        emoji = get_module_emoji(module_inst.icon, module_inst)
        title = QLabel(f"{emoji} {mod_name} - {language.tr('tooltip_help')}")
        title.setStyleSheet("font-size: 18px; font-weight: bold; color: #60a5fa;")
        header.addWidget(title)

        ver_badge = QLabel(f"v{getattr(module_inst, 'version', '1.0.0')}")
        ver_badge.setStyleSheet("""
            background-color: rgba(255, 255, 255, 0.08);
            color: #94a3b8;
            font-size: 11px;
            font-weight: bold;
            font-family: 'Menlo', 'Monaco', 'Courier New', monospace;
            padding: 2px 8px;
            border-radius: 4px;
            border: 1px solid rgba(255, 255, 255, 0.15);
        """)
        header.addWidget(ver_badge)

        if not getattr(module_inst, "is_ready", True):
            wip_badge = QLabel(f"[{language.tr('status_wip_label')}]")
            wip_badge.setStyleSheet("""
                background-color: rgba(245, 158, 11, 0.18);
                color: #fbbf24;
                font-size: 11px;
                font-weight: bold;
                padding: 2px 8px;
                border-radius: 4px;
                border: 1px solid rgba(245, 158, 11, 0.35);
            """)
            header.addWidget(wip_badge)

        header.addStretch()
        layout.addLayout(header)

        desc_lbl = QLabel(mod_desc)
        desc_lbl.setStyleSheet("color: #94a3b8; font-size: 13px;")
        desc_lbl.setWordWrap(True)
        layout.addWidget(desc_lbl)

        self.scroll = QScrollArea()
        self.scroll.setWidgetResizable(True)
        self.scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll.setStyleSheet("QScrollArea { border: 1px solid rgba(255,255,255,0.1); border-radius: 8px; background-color: #1e293b; }")

        self.content_widget = QWidget()
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(16, 16, 16, 16)
        self.content_layout.setSpacing(14)

        # Resolve module help/setup guide image file (.png, .jpg, .jpeg, .webp)
        # Check explicit help_image_path first, then search module directory
        resolved_img = None
        img_path = getattr(module_inst, "help_image_path", "")

        mod_dir = ""
        try:
            mod_file = inspect.getfile(module_inst.__class__)
            if mod_file and os.path.exists(mod_file):
                mod_dir = os.path.dirname(os.path.abspath(mod_file))
        except Exception:
            pass
        if not mod_dir:
            mod_dir = os.path.join(CARDS_DIR, module_inst.module_id)

        candidates = []
        if img_path:
            candidates.extend([
                img_path,
                os.path.join(mod_dir, img_path),
                os.path.join(BASE_DIR, img_path),
            ])

        image_exts = (".png", ".jpg", ".jpeg", ".webp")
        for base_name in ["setup_guide", "guide", "help", "wiring", "pinout", "schematic", module_inst.module_id]:
            for ext in image_exts:
                candidates.append(os.path.join(mod_dir, f"{base_name}{ext}"))

        for cand in candidates:
            if cand and os.path.isfile(cand):
                resolved_img = cand
                break

        # Fallback: any image file located directly in the module folder
        if not resolved_img and os.path.isdir(mod_dir):
            for fname in sorted(os.listdir(mod_dir)):
                if fname.lower().endswith(image_exts):
                    fpath = os.path.join(mod_dir, fname)
                    if os.path.isfile(fpath):
                        resolved_img = fpath
                        break

        if resolved_img:
            pixmap = QPixmap(resolved_img)
            if not pixmap.isNull():
                self.orig_pixmap = pixmap
                self.img_lbl = QLabel()
                self.img_lbl.setAlignment(Qt.AlignCenter)
                self.img_lbl.setStyleSheet(
                    "background-color: #0f172a; border: 1px solid rgba(255, 255, 255, 0.1); "
                    "border-radius: 8px; padding: 6px;"
                )
                self.content_layout.addWidget(self.img_lbl)

        if not help_text:
            help_text = f"[{mod_name}]\n\nNo detailed help documentation provided."

        self.text_edit = QTextBrowser()
        self.text_edit.setReadOnly(True)
        self.text_edit.setOpenExternalLinks(True)

        escaped_help = html.escape(help_text)
        url_re = re.compile(r"(https?://[^\s<>]+)")
        html_help = url_re.sub(r'<a href="\1" style="color: #60a5fa; text-decoration: underline;">\1</a>', escaped_help)
        html_help = html_help.replace("\n", "<br>")
        self.text_edit.setHtml(
            f'<div style="font-family: \'Courier New\', monospace; font-size: 12px; color: #f8fafc; line-height: 1.6;">'
            f'{html_help}</div>'
        )
        self.text_edit.setStyleSheet("""
            QTextBrowser {
                background-color: #0f172a;
                color: #f8fafc;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255,255,255,0.1);
                border-radius: 6px;
                padding: 10px;
            }
        """)
        self.content_layout.addWidget(self.text_edit)

        self.scroll.setWidget(self.content_widget)
        layout.addWidget(self.scroll)

        btn_close = QPushButton(language.tr("btn_close"))
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                padding: 8px 20px;
                border-radius: 6px;
                font-weight: bold;
                border: 1px solid #60a5fa;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
                border-color: #93c5fd;
                padding-top: 10px;
                padding-left: 22px;
            }
        """)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

        self.update_image_scale()

    def update_image_scale(self):
        if self._updating_image:
            return
        self._updating_image = True
        try:
            vp_w = self.scroll.viewport().width()
            vp_h = self.scroll.viewport().height()

            if hasattr(self, "text_edit") and self.text_edit:
                doc = self.text_edit.document()
                doc.setTextWidth(max(200, vp_w - 50))
                ideal_txt_h = max(60, int(doc.size().height()) + 24)
                self.text_edit.setFixedHeight(ideal_txt_h)
            else:
                ideal_txt_h = 0

            if self.orig_pixmap and not self.orig_pixmap.isNull() and self.img_lbl:
                avail_w = max(100, vp_w - 60)
                avail_h = max(180, vp_h - ideal_txt_h - 60)

                orig_w = self.orig_pixmap.width()
                orig_h = self.orig_pixmap.height()

                # Large images scale down to fit visible area;
                # When window is enlarged, scale up proportionally capped at native resolution
                target_w = min(avail_w, orig_w)
                target_h = min(avail_h, orig_h)

                scaled = self.orig_pixmap.scaled(
                    target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
                )
                self.img_lbl.setPixmap(scaled)
        finally:
            self._updating_image = False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_image_scale()

    def showEvent(self, event):
        super().showEvent(event)
        self.update_image_scale()


# ==============================================================================
# 5. Dialog Setup Classes (Serial & Firmware & Criteria Thresholds)
# ==============================================================================
# ==============================================================================
# 5. Settings dialogs
#
# The three classes SerialConfigSetupDialog / FirmwareConfigSetupDialog /
# CriteriaDialog were replaced by the single ui_qt.schema_form.
# SchemaCriteriaDialog. It builds the form purely from the type / options /
# accept and actions a module declares, so adding a module never requires
# editing this file. make_criteria_dialog() is the entry point.
# ==============================================================================


class DetailDialog(QDialog):
    def __init__(self, card_widget: TestCardWidget, parent=None):
        super().__init__(parent)
        self.card = card_widget
        self.last_log_count = -1
        mod_name = card_widget.module.get_localized("name", language.current_lang)

        self.setWindowTitle(f"Detail Result - {mod_name}")
        self.resize(750, 650)
        self.setStyleSheet("background-color: #0f172a; color: #f8fafc;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(16)

        header = QHBoxLayout()
        emoji = get_module_emoji(card_widget.module.icon, card_widget.module)
        title = QLabel(f"{emoji} {mod_name}")
        title.setStyleSheet("font-size: 18px; font-weight: bold;")
        
        self.status_lbl = QLabel()
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.status_lbl)
        layout.addLayout(header)

        self.figure = Figure(figsize=(6, 3), facecolor="#1e293b")
        self.canvas = FigureCanvas(self.figure)
        layout.addWidget(self.canvas)

        self.metrics_tbl = QTableWidget()
        self.metrics_tbl.setColumnCount(2)
        self.metrics_tbl.setHorizontalHeaderLabels(["Metric", "Value"])
        self.metrics_tbl.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.metrics_tbl.setStyleSheet("""
            QTableWidget {
                background-color: #1e293b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #334155;
                color: #f8fafc;
                padding: 6px;
                border: none;
            }
        """)
        layout.addWidget(self.metrics_tbl)

        log_title = QLabel(language.tr("report_log_stream_title"))
        log_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #60a5fa;")
        layout.addWidget(log_title)

        self.log_edit = QTextEdit()
        self.log_edit.setReadOnly(True)
        self.log_edit.setStyleSheet("""
            QTextEdit {
                background-color: #090d16;
                color: #a7f3d0;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }
        """)
        layout.addWidget(self.log_edit)

        btn_close = QPushButton(language.tr("btn_close"))
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                padding: 8px 20px;
                border-radius: 6px;
                font-weight: bold;
                border: 1px solid #60a5fa;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
                border-color: #93c5fd;
                padding-top: 10px;
                padding-left: 22px;
            }
        """)
        btn_close.clicked.connect(self.accept)
        layout.addWidget(btn_close, alignment=Qt.AlignRight)

        # Setup real-time live refresh timer
        self.live_timer = QTimer(self)
        self.live_timer.setInterval(200)
        self.live_timer.timeout.connect(self._refresh_live_details)
        self.live_timer.start()
        self._refresh_live_details()

    def _refresh_live_details(self):
        st = self.card.status
        ex = self.card.execution_time
        self.status_lbl.setText(f" {st} ({ex}) ")
        if st == "PASS":
            self.status_lbl.setStyleSheet("background-color: #10b981; color: white; font-weight: bold; border-radius: 6px; padding: 4px 12px;")
        elif st == "FAIL":
            self.status_lbl.setStyleSheet("background-color: #ef4444; color: white; font-weight: bold; border-radius: 6px; padding: 4px 12px;")
        else:
            self.status_lbl.setStyleSheet("background-color: #f59e0b; color: white; font-weight: bold; border-radius: 6px; padding: 4px 12px;")

        if st == "RUNNING" or not self.card.last_result:
            current_logs = getattr(self.card, "live_logs", [])
        else:
            current_logs = self.card.last_result.get("details", {}).get("logs", getattr(self.card, "live_logs", []))

        if not current_logs:
            current_logs = ["[INFO] Test initializing or waiting for log output..."]

        if len(current_logs) != self.last_log_count:
            self.last_log_count = len(current_logs)
            self.log_edit.setText("\n".join(current_logs))
            cursor = self.log_edit.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.log_edit.setTextCursor(cursor)

        if self.card.last_result and "details" in self.card.last_result:
            metrics = self.card.last_result["details"].get("metrics", {})
            self.metrics_tbl.setRowCount(len(metrics))
            for idx, (k, v) in enumerate(metrics.items()):
                self.metrics_tbl.setItem(idx, 0, QTableWidgetItem(str(k)))
                self.metrics_tbl.setItem(idx, 1, QTableWidgetItem(str(v)))
            self.plot_chart()

    def plot_chart(self):
        self.figure.clear()
        ax = self.figure.add_subplot(111)
        ax.set_facecolor("#1e293b")
        ax.tick_params(colors="#94a3b8")
        for spine in ax.spines.values():
            spine.set_color("#475569")

        chart_info = (self.card.last_result or {}).get("details", {}).get("chart")
        if chart_info and chart_info.get("labels"):
            labels = chart_info["labels"]
            # A three-point min/avg/peak chart wants markers; a waveform of
            # several hundred buckets does not - markers there merge into a
            # solid band and hide the shape the chart exists to show.
            dense = len(labels) > DENSE_CHART_POINTS
            for ds in chart_info["datasets"]:
                ax.plot(
                    labels, ds["data"], label=ds["label"],
                    marker=None if dense else "o",
                    linewidth=1.0 if dense else 1.8,
                    alpha=float(ds.get("alpha", 1.0)),
                    color=ds.get("borderColor", "#38ef7d"),
                )
            if chart_info.get("x_label"):
                ax.set_xlabel(chart_info["x_label"], color="#94a3b8", fontsize=9)
            if chart_info.get("y_label"):
                ax.set_ylabel(chart_info["y_label"], color="#94a3b8", fontsize=9)
            ax.legend(facecolor="#1e293b", edgecolor="#475569", labelcolor="#f8fafc",
                      fontsize=8)
            ax.grid(True, linestyle="--", alpha=0.3)
            if dense:
                # Waveform: no padding on the time axis, so the window shown is
                # exactly the window measured.
                ax.set_xlim(labels[0], labels[-1])
                self.figure.tight_layout()
        else:
            ax.text(0.5, 0.5, "No Measurement Data", color="#94a3b8", ha="center", va="center")

        self.canvas.draw()


# ==============================================================================
# 7. Test Result Summary Report Dialog (TestReportDialog)
# ==============================================================================
class TestReportDialog(QDialog):
    def __init__(self, report_data: dict, parent=None):
        super().__init__(parent)
        self.report_data = report_data
        self.setWindowTitle(language.tr("report_title"))
        self.resize(850, 700)
        self.setStyleSheet("background-color: #0f172a; color: #f8fafc;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)
        layout.setSpacing(14)

        cards_summary = report_data.get("cards_summary", [])
        total_count = len(cards_summary)
        pass_count = sum(1 for c in cards_summary if c["status"] == "PASS")
        fail_count = sum(1 for c in cards_summary if c["status"] == "FAIL")
        total_time = sum(c.get("execution_time_sec", 0.0) for c in cards_summary)

        is_overall_pass = (fail_count == 0 and total_count > 0)
        overall_status_str = "OVERALL PASS" if is_overall_pass else "OVERALL FAIL"
        overall_bg = "#10b981" if is_overall_pass else "#ef4444"

        header_box = QHBoxLayout()
        title_lbl = QLabel(language.tr("report_title"))
        title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #60a5fa;")
        
        badge = QLabel(f" {overall_status_str} ")
        badge.setStyleSheet(f"background-color: {overall_bg}; color: white; font-weight: bold; border-radius: 6px; padding: 6px 14px; font-size: 14px;")

        header_box.addWidget(title_lbl)
        header_box.addStretch()
        header_box.addWidget(badge)
        layout.addLayout(header_box)

        stats_box = QHBoxLayout()
        lbl_stats = QLabel(f"{language.tr('report_total')}: {total_count}  |  ✅ {language.tr('report_pass')}: {pass_count}  |  ❌ {language.tr('report_fail')}: {fail_count}  |  ⏱️ {language.tr('report_time')}: {total_time:.2f}s")
        lbl_stats.setStyleSheet("background-color: #1e293b; padding: 10px 14px; border-radius: 8px; font-weight: bold; font-size: 13px; color: #cbd5e1;")
        stats_box.addWidget(lbl_stats)
        layout.addLayout(stats_box)

        table = QTableWidget()
        table.setColumnCount(5)
        table.setHorizontalHeaderLabels([
            language.tr("report_tbl_no"),
            language.tr("report_tbl_name"),
            language.tr("report_tbl_time"),
            language.tr("report_tbl_status"),
            language.tr("report_tbl_summary")
        ])
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setSectionResizeMode(4, QHeaderView.Stretch)
        table.setStyleSheet("""
            QTableWidget {
                background-color: #1e293b;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }
            QHeaderView::section {
                background-color: #334155;
                color: #f8fafc;
                padding: 6px;
                border: none;
            }
        """)

        table.setRowCount(total_count)
        for idx, item in enumerate(cards_summary):
            table.setItem(idx, 0, QTableWidgetItem(str(idx + 1)))
            table.setItem(idx, 1, QTableWidgetItem(item["name"]))
            table.setItem(idx, 2, QTableWidgetItem(f"{item['execution_time_sec']:.2f}s"))
            
            status_item = QTableWidgetItem(item["status"])
            if item["status"] == "PASS":
                status_item.setForeground(QColor("#10b981"))
            else:
                status_item.setForeground(QColor("#ef4444"))
            table.setItem(idx, 3, status_item)
            
            table.setItem(idx, 4, QTableWidgetItem(item["summary_text"]))

        # Clicking a row jumps to that item's logs below. Whole-row selection so
        # the click target is the row, not one cell.
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.SingleSelection)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.verticalHeader().setVisible(False)
        table.setToolTip(language.tr("report_tbl_click_hint"))
        table.cellClicked.connect(self._on_row_clicked)
        self.table = table

        layout.addWidget(table)

        log_header = QHBoxLayout()
        log_title = QLabel(language.tr("report_log_title"))
        log_title.setStyleSheet("font-weight: bold; font-size: 13px; color: #94a3b8;")
        log_header.addWidget(log_title)
        # Shows which row the log view was scrolled to, so the jump is visible
        # even when the section header scrolls just off the top.
        self.lbl_jump_hint = QLabel(language.tr("report_tbl_click_hint"))
        self.lbl_jump_hint.setStyleSheet("font-size: 11px; color: #64748b;")
        log_header.addStretch()
        log_header.addWidget(self.lbl_jump_hint)
        layout.addLayout(log_header)

        log_edit = QTextEdit()
        log_edit.setReadOnly(True)
        log_edit.setStyleSheet("""
            QTextEdit {
                background-color: #090d16;
                color: #a7f3d0;
                font-family: 'Courier New', monospace;
                font-size: 12px;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 8px;
            }
        """)
        all_logs = report_data.get("all_logs", [])
        log_edit.setText("\n".join(all_logs))
        self.log_edit = log_edit
        self._all_logs = list(all_logs)
        layout.addWidget(log_edit)

        btn_box = QHBoxLayout()
        btn_export_txt = QPushButton(language.tr("btn_export_txt"))
        btn_export_html = QPushButton(language.tr("btn_export_html"))
        btn_close = QPushButton(language.tr("btn_close"))

        btn_export_txt.setStyleSheet("""
            QPushButton {
                background-color: #334155;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:hover {
                background-color: #475569;
            }
            QPushButton:pressed {
                background-color: #1e293b;
                padding-top: 10px;
                padding-left: 18px;
            }
        """)
        btn_export_html.setStyleSheet("""
            QPushButton {
                background-color: #3b82f6;
                color: white;
                font-weight: bold;
                padding: 8px 16px;
                border-radius: 6px;
                border: 1px solid #60a5fa;
            }
            QPushButton:hover {
                background-color: #2563eb;
            }
            QPushButton:pressed {
                background-color: #1d4ed8;
                border-color: #93c5fd;
                padding-top: 10px;
                padding-left: 18px;
            }
        """)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #475569;
                color: white;
                padding: 8px 16px;
                border-radius: 6px;
                border: 1px solid rgba(255, 255, 255, 0.1);
            }
            QPushButton:hover {
                background-color: #64748b;
            }
            QPushButton:pressed {
                background-color: #334155;
                padding-top: 10px;
                padding-left: 18px;
            }
        """)

        btn_open_html = QPushButton(language.tr("btn_open_html"))
        btn_open_html.setStyleSheet(btn_export_html.styleSheet())

        btn_export_txt.clicked.connect(self.export_txt_report)
        btn_export_html.clicked.connect(self.export_html_report)
        btn_open_html.clicked.connect(self.open_html_report)
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(btn_export_txt)
        btn_box.addWidget(btn_export_html)
        btn_box.addWidget(btn_open_html)
        btn_box.addStretch()
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    # ------------------------------------------------------ table -> log jump
    def _log_line_of(self, log_index: int) -> int:
        """
        Text line number where all_logs[log_index] starts.

        The list index is not the line number: entries are joined with "\n" and
        a section header entry itself begins with "\n", so one entry can occupy
        two lines. Each entry occupies 1 + its own newline count.
        """
        line = 0
        for entry in self._all_logs[:log_index]:
            line += 1 + str(entry).count("\n")
        # The header entry opens with a blank line; land on the visible text.
        header = str(self._all_logs[log_index]) if log_index < len(self._all_logs) else ""
        return line + (1 if header.startswith("\n") else 0)

    def _on_row_clicked(self, row: int, _column: int = 0) -> None:
        """Scroll the log view to the clicked item's section and select its header."""
        summary = self.report_data.get("cards_summary", [])
        if not (0 <= row < len(summary)):
            return
        log_index = summary[row].get("log_index")
        if log_index is None:
            # A step that produced no logs - say so rather than leaving the view
            # where it was, which reads as "the click did nothing".
            self.log_edit.moveCursor(QTextCursor.End)
            name = summary[row].get("name", "")
            if hasattr(self, "lbl_jump_hint"):
                self.lbl_jump_hint.setText(language.tr("report_no_logs_for", name=name))
            return

        target_line = self._log_line_of(log_index)
        doc = self.log_edit.document()
        block = doc.findBlockByLineNumber(min(target_line, doc.blockCount() - 1))

        cursor = QTextCursor(block)
        cursor.select(QTextCursor.LineUnderCursor)
        self.log_edit.setTextCursor(cursor)

        # Put the section at the top of the view rather than merely visible, so
        # the lines that follow it are the ones on screen.
        self.log_edit.ensureCursorVisible()
        bar = self.log_edit.verticalScrollBar()
        bar.setValue(min(bar.maximum(), bar.value() + self.log_edit.cursorRect().top()))

        if hasattr(self, "lbl_jump_hint"):
            self.lbl_jump_hint.setText(
                language.tr("report_jumped_to", name=summary[row].get("name", ""))
            )

    def export_txt_report(self):
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save TXT Report", f"nRF_Test_Report_{time.strftime('%Y%m%d_%H%M%S')}.txt", "Text Files (*.txt)"
        )
        if not file_path:
            return
        
        cards_summary = self.report_data.get("cards_summary", [])
        total_count = len(cards_summary)
        pass_count = sum(1 for c in cards_summary if c["status"] == "PASS")
        fail_count = sum(1 for c in cards_summary if c["status"] == "FAIL")
        total_time = sum(c.get("execution_time_sec", 0.0) for c in cards_summary)

        lines = [
            "==========================================================================",
            "                 nRF DTM & Test Suite Summary Report                      ",
            "==========================================================================",
            f"Generated At  : {time.strftime('%Y-%m-%d %H:%M:%S')}",
            f"Overall Result: {'PASS' if fail_count == 0 and total_count > 0 else 'FAIL'}",
            f"Total Tests   : {total_count}",
            f"Pass / Fail   : PASS {pass_count} / FAIL {fail_count}",
            f"Total Elapsed : {total_time:.2f} seconds",
            "--------------------------------------------------------------------------",
            "ITEM SUMMARY:",
        ]

        for idx, c in enumerate(cards_summary):
            lines.append(f"  [{idx+1:02d}] {c['name']:<30} | {c['status']:<4} | {c['execution_time_sec']:>5.2f}s | {c['summary_text']}")

        lines.extend([
            "--------------------------------------------------------------------------",
            "FULL EXECUTION LOGS:",
            "--------------------------------------------------------------------------"
        ])
        lines.extend(self.report_data.get("all_logs", []))
        lines.append("==========================================================================")

        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write("\n".join(lines))
            QMessageBox.information(self, language.tr("export_complete_title"),
                                    language.tr("export_complete_body", path=file_path))
        except Exception as e:
            QMessageBox.warning(self, language.tr("export_error_title"),
                                language.tr("export_error_body", error=e))

    def export_html_report(self):
        """Write the report to a file the user picks."""
        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save HTML Report", f"nRF_Test_Report_{time.strftime('%Y%m%d_%H%M%S')}.html", "HTML Files (*.html)"
        )
        if not file_path:
            return
        try:
            with open(file_path, "w", encoding="utf-8") as f:
                f.write(self._build_html_report())
            QMessageBox.information(self, language.tr("export_complete_title"),
                                    language.tr("export_complete_body", path=file_path))
        except Exception as e:
            QMessageBox.warning(self, language.tr("export_error_title"),
                                language.tr("export_error_body", error=e))

    def open_html_report(self):
        """
        Show the report in the operator's own browser.

        Written to the temp directory rather than asking for a location: this is
        for looking at the report, not keeping it, and a save dialog in the way
        of "let me read this properly" is friction. Saving is the other button.

        The file is deliberately not deleted afterwards - the browser reads it
        after this returns, so removing it here would sometimes race and show a
        blank tab. It lives in the temp directory, which is the operating
        system's to clean.
        """
        stamp = time.strftime("%Y%m%d_%H%M%S")
        path = os.path.join(tempfile.gettempdir(), f"nRF_Test_Report_{stamp}.html")
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self._build_html_report())
        except Exception as e:
            QMessageBox.warning(self, language.tr("export_error_title"),
                                language.tr("export_error_body", error=e))
            return
        if not QDesktopServices.openUrl(QUrl.fromLocalFile(path)):
            # Report it rather than leaving a button that looks like it did
            # nothing; the file is written, so the path is still useful.
            QMessageBox.warning(self, language.tr("export_error_title"),
                                language.tr("open_browser_failed", path=path))

    def _build_html_report(self) -> str:
        """The report as a standalone HTML document, for saving or for viewing."""
        cards_summary = self.report_data.get("cards_summary", [])
        total_count = len(cards_summary)
        pass_count = sum(1 for c in cards_summary if c["status"] == "PASS")
        fail_count = sum(1 for c in cards_summary if c["status"] == "FAIL")
        total_time = sum(c.get("execution_time_sec", 0.0) for c in cards_summary)

        is_overall_pass = (fail_count == 0 and total_count > 0)

        table_rows_html = ""
        for idx, c in enumerate(cards_summary):
            badge_cls = "pass" if c["status"] == "PASS" else "fail"
            table_rows_html += f"""
            <tr>
                <td>{idx+1}</td>
                <td><strong>{c['name']}</strong></td>
                <td>{c['execution_time_sec']:.2f}s</td>
                <td><span class="badge {badge_cls}">{c['status']}</span></td>
                <td>{c['summary_text']}</td>
            </tr>
            """

        logs_html = "<br>".join(self.report_data.get("all_logs", []))

        html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <title>nRF Test Suite Summary Report</title>
    <style>
        body {{ background-color: #0f172a; color: #f8fafc; font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; padding: 30px; margin: 0; }}
        .card {{ background-color: #1e293b; border-radius: 12px; padding: 24px; margin-bottom: 20px; box-shadow: 0 4px 12px rgba(0,0,0,0.3); }}
        h1 {{ color: #3b82f6; margin-top: 0; }}
        .badge {{ padding: 6px 14px; border-radius: 6px; font-weight: bold; font-size: 14px; display: inline-block; }}
        .badge.pass {{ background-color: #10b981; color: white; }}
        .badge.fail {{ background-color: #ef4444; color: white; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 15px; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid rgba(255,255,255,0.1); }}
        th {{ background-color: #334155; color: #f8fafc; }}
        .logs {{ background-color: #090d16; color: #a7f3d0; font-family: monospace; padding: 16px; border-radius: 8px; font-size: 12px; line-height: 1.5; overflow-x: auto; }}
    </style>
</head>
<body>
    <div class="card">
        <div style="display: flex; justify-content: space-between; align-items: center;">
            <h1>📊 nRF DTM & Test Suite Report</h1>
            <span class="badge {'pass' if is_overall_pass else 'fail'}">{'OVERALL PASS' if is_overall_pass else 'OVERALL FAIL'}</span>
        </div>
        <p style="color: #94a3b8;">Generated At: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
        <hr style="border: 0; border-top: 1px solid rgba(255,255,255,0.1);">
        <p><strong>Total Tests:</strong> {total_count} | <strong>PASS:</strong> {pass_count} | <strong>FAIL:</strong> {fail_count} | <strong>Total Elapsed:</strong> {total_time:.2f}s</p>
    </div>

    <div class="card">
        <h2>📋 Test Item Details</h2>
        <table>
            <thead>
                <tr>
                    <th>#</th>
                    <th>Test Name</th>
                    <th>Duration</th>
                    <th>Status</th>
                    <th>Summary</th>
                </tr>
            </thead>
            <tbody>
                {table_rows_html}
            </tbody>
        </table>
    </div>

    <div class="card">
        <h2>💻 Execution Logs</h2>
        <div class="logs">
            {logs_html}
        </div>
    </div>
</body>
</html>"""
        return html_content


# ==============================================================================
# 8. Main Desktop GUI Window (MainWindow)
# ==============================================================================
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle(language.tr("app_title"))
        self.resize(1100, 750)
        self.last_report_data = None
        self.is_running_all = False
        self.stop_requested = False
        self.sequence_thread = None
        self._sequence_logs = {}
        self.last_run_record = None

        # --- production operating state ---
        # Defaults to engineer mode. A production deployment must set a passcode
        # and start in operator mode instead.
        self.mode_controller = ModeController(AppMode.ENGINEER, passcode=None)
        self.recipe = Recipe(name="Untitled Recipe", version="0.1")
        self.station_name = os.environ.get("NRF_STATION", socket.gethostname())
        self.operator_name = os.environ.get("NRF_OPERATOR", getpass.getuser())
        self.current_dut_serial = ""
        self.stop_on_fail = False
        self.stop_batch_on_fail = False
        self.trace_log = TraceLog(os.path.join(BASE_DIR, TRACE_DIR_NAME))
        # Yield counters are scoped to today. Counting the whole log means that
        # as runs pile up, one new result barely moves the percentage and the
        # counters look like they are not working. What a line needs is today's
        # or the current lot's figures anyway, not the all-time total.
        self.yield_scope_since = self._today_start()
        self.yield_scope_label = ""      # for display; filled in by update_yield_display
        self.yield_stats = self.trace_log.stats(since=self.yield_scope_since)

        load_test_modules()
        self.init_ui()

    def init_ui(self):
        self.setStyleSheet("""
            QMainWindow {
                background-color: #0f172a;
            }
            QLabel {
                color: #f8fafc;
            }
            QScrollArea {
                border: none;
                background-color: transparent;
            }
        """)

        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_vbox = QVBoxLayout(central_widget)
        main_vbox.setContentsMargins(20, 20, 20, 20)

        # Top Control Bar
        top_bar = QHBoxLayout()
        self.logo_lbl = QLabel(language.tr("logo_title"))
        self.logo_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #3b82f6;")

        # Rarely used items moved to the menu bar. QAction supports isChecked /
        # setChecked / setEnabled / setText / setVisible, so the attribute names
        # existing code referenced could stay exactly as they were.
        self.build_menu_bar()

        # ======================================================================
        # The top bar keeps only what is watched and operated while running.
        # Language, mock mode, auto-report, module reload and recipe import/export
        # moved to the menu bar: with everything on one row the window does not
        # even fit a FullHD screen.
        # ======================================================================
        self.btn_mode = QPushButton()
        self.btn_mode.setToolTip(language.tr("tooltip_mode_toggle"))
        self.btn_mode.clicked.connect(self.on_mode_toggle_clicked)

        self.recipe_label = QPushButton()
        self.recipe_label.setFlat(True)
        self.recipe_label.setCursor(Qt.PointingHandCursor)
        self.recipe_label.setStyleSheet(
            "QPushButton { color: #f8fafc; font-weight: bold; border: none; padding: 4px 8px; text-align: left; }"
            "QPushButton:hover { color: #60a5fa; }"
        )
        self.recipe_label.clicked.connect(self.on_recipe_info_clicked)
        self.btn_recipe_info = self.recipe_label

        self.recipe_fp_label = QLabel("-")
        self.recipe_fp_label.setToolTip(language.tr("tooltip_recipe_fp"))
        self.recipe_fp_label.setStyleSheet(
            "color: #64748b; font-size: 11px; font-family: 'Courier New', Menlo, monospace;"
        )

        self.dut_label = QLabel(language.tr("dut_unset"))
        self.dut_label.setStyleSheet(
            "color: #fbbf24; font-size: 12px; font-weight: bold; padding: 0 6px;"
        )

        # Number of full-sequence repeats. 0 repeats until stopped (burn-in).
        # Named in words rather than with a repeat glyph: the icon sat next to a
        # bare number and did not say what the number counted.
        self.lbl_seq_repeat = QLabel(language.tr("lbl_seq_repeat_text"))
        self.lbl_seq_repeat.setStyleSheet(
            "color: #94a3b8; font-size: 12px; font-weight: bold;"
        )
        self.lbl_seq_repeat.setToolTip(language.tr("tooltip_seq_repeat_label"))
        self.spin_seq_repeat = QSpinBox()
        self.spin_seq_repeat.setRange(0, 100000)
        self.spin_seq_repeat.setValue(1)
        self.spin_seq_repeat.setSpecialValueText("∞")
        self.spin_seq_repeat.setToolTip(language.tr("tooltip_seq_repeat"))
        self.spin_seq_repeat.valueChanged.connect(lambda _v: self.save_dashboard_state())
        # Explicit step buttons: the macOS style draws no arrows inside a
        # stylesheet-themed spin box, so they are ordinary buttons instead.
        self.seq_repeat_box = with_steppers(self.spin_seq_repeat, field_width=52)
        self.seq_repeat_box.setToolTip(language.tr("tooltip_seq_repeat"))

        # Yield counters (operator mode)
        self.yield_container = QWidget()
        yield_box = QHBoxLayout(self.yield_container)
        yield_box.setContentsMargins(10, 2, 10, 2)
        yield_box.setSpacing(12)
        self.lbl_yield_total = QLabel("0")
        self.lbl_yield_pass = QLabel("0")
        self.lbl_yield_fail = QLabel("0")
        self.lbl_yield_pct = QLabel("0.0%")
        self._yield_captions = []
        for key, widget, color in (
            ("yield_total", self.lbl_yield_total, "#94a3b8"),
            ("yield_pass", self.lbl_yield_pass, "#10b981"),
            ("yield_fail", self.lbl_yield_fail, "#ef4444"),
            ("yield_rate", self.lbl_yield_pct, "#60a5fa"),
        ):
            cap = QLabel(language.tr(key))
            self._yield_captions.append((cap, key))
            cap.setStyleSheet("color: #64748b; font-size: 10px;")
            widget.setStyleSheet(f"color: {color}; font-size: 15px; font-weight: bold;")
            cell = QVBoxLayout()
            cell.setSpacing(0)
            cell.addWidget(cap, alignment=Qt.AlignHCenter)
            cell.addWidget(widget, alignment=Qt.AlignHCenter)
            yield_box.addLayout(cell)
        self.yield_container.setStyleSheet(
            "background-color: #1e293b; border: 1px solid rgba(255,255,255,0.08); border-radius: 6px;"
        )

        # Trash can button to clear/reset all test cards back to initial pending state
        self.btn_clear_results_bar = QPushButton("🗑️")
        self.btn_clear_results_bar.setToolTip(language.tr("tooltip_clear_results"))
        self.btn_clear_results_bar.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.15);
                padding: 6px 12px;
                border-radius: 8px;
                font-size: 15px;
            }
            QPushButton:hover {
                background-color: rgba(239, 68, 68, 0.25);
                color: #fca5a5;
                border-color: #ef4444;
            }
            QPushButton:pressed {
                background-color: #ef4444;
                color: white;
                border-color: #f87171;
            }
            QPushButton:disabled {
                background-color: transparent;
                color: #475569;
                border-color: #334155;
            }
        """)
        self.btn_clear_results_bar.clicked.connect(self.clear_all_results)

        # Viewing the report is on the menu too, but it is the button pressed
        # most often right after a run, so it also sits on the top bar. Binding
        # the menu action to a QToolButton keeps text, tooltip and enabled state
        # in sync automatically, with no duplicated retranslation code.
        self.btn_view_report_bar = QToolButton()
        self.btn_view_report_bar.setDefaultAction(self.btn_view_report)
        self.btn_view_report_bar.setToolButtonStyle(Qt.ToolButtonTextOnly)
        self.btn_view_report_bar.setStyleSheet(TOOLBAR_BTN_STYLE)

        self.btn_run_all = QPushButton(language.tr("btn_run_all"))
        self.btn_run_all.clicked.connect(self.on_btn_run_all_clicked)

        # Let the logo and recipe name shrink so they do not eat the width on a
        # narrow screen.
        self.logo_lbl.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)
        self.recipe_label.setSizePolicy(QSizePolicy.Maximum, QSizePolicy.Preferred)

        top_bar.addWidget(self.logo_lbl)
        top_bar.addSpacing(12)
        top_bar.addWidget(self.btn_mode)
        top_bar.addSpacing(8)
        top_bar.addWidget(self.recipe_label)
        top_bar.addWidget(self.recipe_fp_label)
        top_bar.addStretch(1)
        top_bar.addWidget(self.dut_label)
        top_bar.addWidget(self.yield_container)
        top_bar.addSpacing(6)
        top_bar.addWidget(self.btn_clear_results_bar)
        top_bar.addWidget(self.btn_view_report_bar)
        top_bar.addWidget(self.btn_run_all)
        main_vbox.addLayout(top_bar)
        main_vbox.addSpacing(6)

        # Global Hardware Connection Toolbar
        self.device_toolbar = self.build_device_toolbar()
        main_vbox.addWidget(self.device_toolbar)
        main_vbox.addSpacing(6)

        splitter = QSplitter(Qt.Horizontal)

        left_widget = QWidget()
        left_vbox = QVBoxLayout(left_widget)
        left_vbox.setContentsMargins(0, 0, 0, 0)

        # The palette is hidden in operator mode: an operator must not be able
        # to change the sequence.
        self.palette_container = QWidget()
        palette_vbox = QVBoxLayout(self.palette_container)
        palette_vbox.setContentsMargins(0, 0, 0, 0)
        left_vbox.addWidget(self.palette_container)
        left_vbox = palette_vbox

        self.palette_title = QLabel(language.tr("palette_title"))
        self.palette_title.setStyleSheet("font-weight: bold; font-size: 14px; margin-bottom: 6px;")
        left_vbox.addWidget(self.palette_title)

        # Search box. Filters on name, description, tags and module_id, so a
        # station with dozens of installed modules stays usable.
        self.palette_search = QLineEdit()
        self.palette_search.setPlaceholderText(language.tr("palette_search_placeholder"))
        self.palette_search.setClearButtonEnabled(True)
        self.palette_search.setStyleSheet("""
            QLineEdit {
                background-color: #0f172a;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 5px 8px;
                color: #e2e8f0;
                font-size: 12px;
            }
            QLineEdit:focus { border-color: #3b82f6; }
        """)
        self.palette_search.textChanged.connect(self.apply_palette_filter)
        left_vbox.addWidget(self.palette_search)

        # Shown only while a query hides something, so it never adds noise.
        self.palette_result_label = QLabel()
        self.palette_result_label.setStyleSheet(
            "color: #64748b; font-size: 11px; padding: 2px 2px 4px 2px;")
        self.palette_result_label.setVisible(False)
        left_vbox.addWidget(self.palette_result_label)

        self.palette_list = ModulePaletteWidget()
        left_vbox.addWidget(self.palette_list)

        right_widget = QWidget()
        right_vbox = QVBoxLayout(right_widget)
        right_vbox.setContentsMargins(0, 0, 0, 0)

        # The dashboard title and the sequence-repeat control share one row: the
        # repeat count applies to this list of cards, so it belongs above the
        # list rather than in the window's top bar next to the DUT and yield.
        self.dash_title = QLabel(language.tr("dashboard_title"))
        self.dash_title.setStyleSheet("font-weight: bold; font-size: 14px;")
        dash_header = QHBoxLayout()
        dash_header.setContentsMargins(0, 0, 0, 6)
        dash_header.setSpacing(8)
        dash_header.addWidget(self.dash_title)
        dash_header.addStretch(1)
        dash_header.addWidget(self.lbl_seq_repeat)
        dash_header.addWidget(self.seq_repeat_box)
        right_vbox.addLayout(dash_header)

        self.right_splitter = QSplitter(Qt.Orientation.Vertical)
        self.right_splitter.setStyleSheet("""
            QSplitter::handle:vertical {
                background-color: rgba(255, 255, 255, 0.08);
                height: 4px;
            }
            QSplitter::handle:vertical:hover {
                background-color: #38bdf8;
            }
        """)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        self.drop_zone = DashboardDropZone()
        scroll.setWidget(self.drop_zone)
        self.right_splitter.addWidget(scroll)

        self.live_console = LiveConsoleWidget()
        self.right_splitter.addWidget(self.live_console)
        self.right_splitter.setSizes([520, 180])
        self.right_splitter.setCollapsible(0, False)
        self.right_splitter.setCollapsible(1, True)

        right_vbox.addWidget(self.right_splitter)

        splitter.addWidget(left_widget)
        splitter.addWidget(right_widget)
        splitter.setSizes([280, 780])

        main_vbox.addWidget(splitter)

        self.populate_palette()
        self.build_status_bar()
        self.load_dashboard_state()

        self.update_language_menu()
        self.set_run_all_button_mode(is_stop_mode=False)
        self.update_recipe_display()
        self.apply_mode_policy()
        self.refresh_dependency_badges()

    def build_status_bar(self) -> None:
        """
        The status bar along the bottom of the window.
        It always shows how many of how many items have completed during a run.
        An item repeating 3 times counts as 3: progress is measured in actual
        step executions.
        """
        bar = self.statusBar()
        bar.setStyleSheet("""
            QStatusBar { background-color: #0f172a; color: #94a3b8; }
            QStatusBar::item { border: none; }
        """)

        self.lbl_progress_text = QLabel(f' {language.tr("status_idle")}')
        self.lbl_progress_text.setStyleSheet("color: #cbd5e1; font-size: 12px; font-weight: bold;")

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedWidth(240)
        self.progress_bar.setFixedHeight(16)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1e293b;
                border: 1px solid rgba(255,255,255,0.12);
                border-radius: 8px;
            }
            QProgressBar::chunk {
                background-color: #3b82f6;
                border-radius: 7px;
            }
        """)

        self.lbl_run_progress = QLabel("")
        self.lbl_run_progress.setStyleSheet("color: #fbbf24; font-size: 12px; font-weight: bold;")

        self.lbl_station_info = QLabel(
            language.tr("status_station_info", station=self.station_name, operator=self.operator_name)
        )
        self.lbl_station_info.setStyleSheet("color: #64748b; font-size: 11px;")
        self.lbl_station_info.setToolTip(language.tr("tooltip_trace_path", path=self.trace_log.jsonl_path))

        bar.addWidget(self.lbl_progress_text)
        bar.addWidget(self.progress_bar)
        bar.addWidget(self.lbl_run_progress)
        bar.addPermanentWidget(self.lbl_station_info)

        self.reset_progress_display()

    def set_progress_text(self, text: str) -> None:
        """
        Set the text on the left of the status bar.

        The leading space is added here, once. There are several call sites, so
        putting it into each string would be easy to forget on the next one.
        """
        self.lbl_progress_text.setText(f" {text}")

    def reset_progress_display(self) -> None:
        recipe = self.build_recipe_from_cards() if self.drop_zone.cards else None
        total = count_step_executions(recipe) if recipe else 0
        self._progress_total = total
        self._progress_done = 0
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(0)
        self.set_progress_text(language.tr("status_idle_items", total=total))
        self.lbl_run_progress.setText("")

    @Slot(int, int)
    def on_sequence_progress(self, done: int, total: int) -> None:
        """Refresh the progress bar as each step execution completes."""
        self._progress_done = done
        self._progress_total = total
        self.progress_bar.setRange(0, max(1, total))
        self.progress_bar.setValue(done)
        pct = (done / total * 100.0) if total else 0.0
        self.set_progress_text(language.tr("status_progress", done=done, total=total, pct=f"{pct:.0f}"))

    @Slot(int, int)
    def on_run_started(self, index: int, total: int) -> None:
        self._current_run_index = index
        if total == 0:
            self.lbl_run_progress.setText(language.tr("status_run_infinite", index=index))
        elif total > 1:
            self.lbl_run_progress.setText(language.tr("status_run_of", index=index, total=total))
        else:
            self.lbl_run_progress.setText("")

        if hasattr(self, "live_console"):
            self.live_console.append_log(f"▶▶ Test Sequence Started (Pass {index}/{total})")

    def build_menu_bar(self) -> None:
        """
        Build the menu bar. Everything on the top toolbar would not fit a FullHD
        width, so whatever is not operated during a run is collected here.
        """
        bar = self.menuBar()
        bar.setStyleSheet("""
            QMenuBar {
                background-color: #0f172a;
                color: #cbd5e1;
                padding: 2px 6px;
            }
            QMenuBar::item { padding: 5px 12px; border-radius: 4px; }
            QMenuBar::item:selected { background-color: #1e293b; color: #ffffff; }
            QMenu {
                background-color: #1e293b;
                color: #f8fafc;
                border: 1px solid rgba(255,255,255,0.15);
                padding: 4px;
            }
            QMenu::item { padding: 6px 24px 6px 22px; border-radius: 4px; }
            QMenu::item:selected { background-color: #3b82f6; }
            QMenu::item:disabled { color: #64748b; }
            QMenu::separator { height: 1px; background: rgba(255,255,255,0.12); margin: 4px 8px; }
        """)

        # --- Recipe ---
        self.menu_recipe = menu_recipe = bar.addMenu(language.tr("menu_recipe"))
        self.act_recipe_info = menu_recipe.addAction(language.tr("menu_recipe_info"))
        self.act_recipe_info.triggered.connect(self.on_recipe_info_clicked)
        menu_recipe.addSeparator()
        self.btn_import_recipe = menu_recipe.addAction(language.tr("menu_recipe_import"))
        self.btn_import_recipe.triggered.connect(self.on_import_recipe_clicked)
        self.btn_export_recipe = menu_recipe.addAction(language.tr("menu_recipe_export"))
        self.btn_export_recipe.triggered.connect(self.on_export_recipe_clicked)

        # --- Test ---
        self.menu_test = menu_test = bar.addMenu(language.tr("menu_test"))
        self.act_run_all_menu = menu_test.addAction(language.tr("btn_run_all"))
        self.act_run_all_menu.triggered.connect(self.on_btn_run_all_clicked)
        menu_test.addSeparator()
        self.btn_clear_results = menu_test.addAction(language.tr("btn_clear_results"))
        self.btn_clear_results.triggered.connect(self.clear_all_results)
        self.btn_view_report = menu_test.addAction(language.tr("btn_view_report"))
        self.btn_view_report.triggered.connect(self.open_last_report)
        menu_test.addSeparator()
        self.act_reset_yield = menu_test.addAction(language.tr("menu_reset_yield"))
        self.act_reset_yield.setToolTip(language.tr("tooltip_reset_yield"))
        self.act_reset_yield.triggered.connect(self.reset_yield_counters)
        self.act_open_trace = menu_test.addAction(language.tr("menu_open_trace"))
        self.act_open_trace.triggered.connect(self.open_trace_folder)

        # --- Settings ---
        self.menu_setup = menu_setup = bar.addMenu(language.tr("menu_settings"))
        # Mock is off by default: the tool measures real hardware unless the
        # user explicitly asks for a simulation. Defaulting to on means a fresh
        # install silently reports simulated results as if they were measured.
        self.mock_checkbox = menu_setup.addAction(language.tr("mock_mode"))
        self.mock_checkbox.setCheckable(True)
        self.mock_checkbox.setChecked(False)
        self.mock_checkbox.toggled.connect(self._on_mock_mode_toggled)

        self.auto_report_checkbox = menu_setup.addAction(language.tr("auto_report"))
        self.auto_report_checkbox.setCheckable(True)
        self.auto_report_checkbox.setChecked(True)
        self.auto_report_checkbox.toggled.connect(lambda _c: self.save_dashboard_state())

        self.act_stop_on_fail = menu_setup.addAction(language.tr("menu_stop_on_fail"))
        self.act_stop_on_fail.setCheckable(True)
        self.act_stop_on_fail.setChecked(self.stop_on_fail)
        self.act_stop_on_fail.toggled.connect(self._on_stop_on_fail_toggled)

        self.act_stop_batch_on_fail = menu_setup.addAction(language.tr("menu_stop_batch_on_fail"))
        self.act_stop_batch_on_fail.setCheckable(True)
        self.act_stop_batch_on_fail.setChecked(self.stop_batch_on_fail)
        self.act_stop_batch_on_fail.setToolTip(language.tr("tooltip_stop_batch_on_fail"))
        self.act_stop_batch_on_fail.toggled.connect(self._on_stop_batch_on_fail_toggled)

        menu_setup.addSeparator()
        self.btn_reload = menu_setup.addAction(language.tr("btn_reload"))
        self.btn_reload.triggered.connect(self.reload_modules_ui)
        menu_setup.addSeparator()
        self.menu_lang = menu_setup.addMenu(language.tr("menu_language"))
        self.lang_action_group = QActionGroup(self)
        self.lang_action_group.setExclusive(True)

        # --- Mode ---
        self.menu_mode = menu_mode = bar.addMenu(language.tr("menu_mode"))
        self.act_toggle_mode = menu_mode.addAction(language.tr("menu_toggle_mode"))
        self.act_toggle_mode.triggered.connect(self.on_mode_toggle_clicked)

    def _on_mock_mode_toggled(self, checked: bool) -> None:
        self.save_dashboard_state()
        if hasattr(self, "drop_zone") and hasattr(self.drop_zone, "cards"):
            for card_w in self.drop_zone.cards.values():
                if hasattr(card_w, "update_style"):
                    card_w.update_style()
        if hasattr(self, "populate_palette"):
            self.populate_palette()

    def _on_stop_on_fail_toggled(self, checked: bool) -> None:
        self.stop_on_fail = checked
        self.save_dashboard_state()

    def _on_stop_batch_on_fail_toggled(self, checked: bool) -> None:
        self.stop_batch_on_fail = checked
        self.save_dashboard_state()

    def open_trace_folder(self) -> None:
        """Open the trace log folder in the file browser."""
        path = self.trace_log.directory
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))

    def update_language_menu(self):
        """
        Rebuild the menu from the discovered languages, including those bundled
        with modules. Reloading modules can introduce a new language, so this is
        rebuilt every time rather than cached.
        """
        for action in list(self.lang_action_group.actions()):
            self.lang_action_group.removeAction(action)
        self.menu_lang.clear()

        for code in language.discover_languages(LOADED_MODULES):
            display_name = LANGUAGE_NAMES.get(code, f"{code.upper()} ({code})")
            action = self.menu_lang.addAction(display_name)
            action.setCheckable(True)
            action.setData(code)
            action.setChecked(code == language.current_lang)
            self.lang_action_group.addAction(action)
            action.triggered.connect(lambda _checked=False, c=code: self.on_language_changed(c))

    def on_language_changed(self, lang_code: str):
        if not lang_code or lang_code == language.current_lang:
            return
        language.set_language(lang_code)
        self.retranslate_ui()
        self.save_dashboard_state()

    def retranslate_ui(self):
        """
        Re-read every displayed string when the language changes.

        A new widget that is not registered here stays in the previous language
        while everything else switches: the language.tr() call at construction time
        is evaluated exactly once.
        """
        self.setWindowTitle(language.tr("app_title"))
        self.logo_lbl.setText(language.tr("logo_title"))
        self.palette_title.setText(language.tr("palette_title"))
        self.palette_search.setPlaceholderText(language.tr("palette_search_placeholder"))
        self.dash_title.setText(language.tr("dashboard_title"))
        self.drop_zone.update_placeholder_text()

        self.retranslate_menus()
        self.retranslate_toolbar()
        self.retranslate_device_toolbar()
        self.retranslate_status_bar()

        self.set_run_all_button_mode(is_stop_mode=self.is_running_all)
        self.update_language_menu()
        self.update_recipe_display()
        self.update_yield_display()
        self.apply_mode_policy()

        self.populate_palette()
        for card_widget in self.drop_zone.cards.values():
            card_widget.update_style()

        if hasattr(self, "live_console"):
            self.live_console.update_language()

    def retranslate_menus(self):
        """Menu bar titles and item labels."""
        self.menu_recipe.setTitle(language.tr("menu_recipe"))
        self.menu_test.setTitle(language.tr("menu_test"))
        self.menu_setup.setTitle(language.tr("menu_settings"))
        self.menu_mode.setTitle(language.tr("menu_mode"))
        self.menu_lang.setTitle(language.tr("menu_language"))

        self.act_recipe_info.setText(language.tr("menu_recipe_info"))
        self.btn_import_recipe.setText(language.tr("menu_recipe_import"))
        self.btn_export_recipe.setText(language.tr("menu_recipe_export"))
        self.act_run_all_menu.setText(language.tr("btn_run_all"))
        self.btn_clear_results.setText(language.tr("btn_clear_results"))
        self.btn_view_report.setText(language.tr("btn_view_report"))
        self.act_reset_yield.setText(language.tr("menu_reset_yield"))
        self.act_reset_yield.setToolTip(language.tr("tooltip_reset_yield"))
        self.act_open_trace.setText(language.tr("menu_open_trace"))
        self.mock_checkbox.setText(language.tr("mock_mode"))
        self.auto_report_checkbox.setText(language.tr("auto_report"))
        self.act_stop_on_fail.setText(language.tr("menu_stop_on_fail"))
        self.act_stop_batch_on_fail.setText(language.tr("menu_stop_batch_on_fail"))
        self.act_stop_batch_on_fail.setToolTip(language.tr("tooltip_stop_batch_on_fail"))
        self.btn_reload.setText(language.tr("btn_reload"))
        self.act_toggle_mode.setText(language.tr("menu_toggle_mode"))

    def retranslate_toolbar(self):
        """Top bar labels and tooltips."""
        self.btn_mode.setToolTip(language.tr("tooltip_mode_toggle"))
        self.recipe_fp_label.setToolTip(language.tr("tooltip_recipe_fp"))
        self.lbl_seq_repeat.setText(language.tr("lbl_seq_repeat_text"))
        self.lbl_seq_repeat.setToolTip(language.tr("tooltip_seq_repeat_label"))
        self.spin_seq_repeat.setToolTip(language.tr("tooltip_seq_repeat"))
        self.btn_clear_results_bar.setToolTip(language.tr("tooltip_clear_results"))
        self.btn_run_all.setText(language.tr("btn_run_all"))
        for caption_label, key in self._yield_captions:
            caption_label.setText(language.tr(key))

    def build_device_toolbar(self) -> QWidget:
        """
        Global Hardware Connection Toolbar.
        Provides station-wide DUT serial port selection, baud rate setting,
        dynamic USB port scanning, port connection testing, and quick terminal launcher.
        """
        container = QWidget()
        container.setObjectName("device_toolbar")
        container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        container.setFixedHeight(44)
        container.setStyleSheet("""
            QWidget#device_toolbar {
                background-color: #1e293b;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
        """)
        layout = QHBoxLayout(container)
        layout.setContentsMargins(12, 4, 12, 4)
        layout.setSpacing(10)

        # Title & Icon
        self.lbl_dev_title = QLabel(language.tr("lbl_dut_port"))
        self.lbl_dev_title.setStyleSheet("color: #38bdf8; font-weight: bold; font-size: 12px;")
        layout.addWidget(self.lbl_dev_title)

        # Port ComboBox
        self.cb_serial_port = QComboBox()
        self.cb_serial_port.setEditable(True)
        self.cb_serial_port.setMinimumWidth(260)
        self.cb_serial_port.setStyleSheet(f"""
            QComboBox {{
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 26px 4px 10px;
                font-size: 12px;
                font-family: Menlo, Consolas, 'Courier New', monospace;
            }}
            QComboBox:hover {{ border-color: #38bdf8; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 20px;
                border: none;
                background: transparent;
                margin-right: 6px;
            }}
            QComboBox::down-arrow {{
                image: url('{ARROW_GRAY_SVG_PATH}');
                width: 10px;
                height: 10px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                outline: 0;
            }}
        """)
        if self.cb_serial_port.lineEdit():
            self.cb_serial_port.lineEdit().setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    color: #f8fafc;
                    border: none;
                    padding: 0px;
                }
            """)
        self.cb_serial_port.currentTextChanged.connect(self.on_serial_device_changed)
        layout.addWidget(self.cb_serial_port)

        # Baud Rate Label & ComboBox
        self.lbl_dev_baud = QLabel(language.tr("lbl_baudrate"))
        self.lbl_dev_baud.setStyleSheet("color: #94a3b8; font-size: 11px;")
        layout.addWidget(self.lbl_dev_baud)

        self.cb_serial_baud = QComboBox()
        self.cb_serial_baud.setEditable(True)
        for rate in [9600, 19200, 38400, 57600, 115200, 230400, 460800, 921600]:
            self.cb_serial_baud.addItem(str(rate))
        self.cb_serial_baud.setCurrentText("115200")
        self.cb_serial_baud.setFixedWidth(105)
        self.cb_serial_baud.setStyleSheet(f"""
            QComboBox {{
                background-color: #0f172a;
                color: #f8fafc;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 22px 4px 8px;
                font-size: 12px;
            }}
            QComboBox:hover {{ border-color: #38bdf8; }}
            QComboBox::drop-down {{
                subcontrol-origin: padding;
                subcontrol-position: center right;
                width: 18px;
                border: none;
                background: transparent;
                margin-right: 4px;
            }}
            QComboBox::down-arrow {{
                image: url('{ARROW_GRAY_SVG_PATH}');
                width: 9px;
                height: 9px;
            }}
            QComboBox QAbstractItemView {{
                background-color: #0f172a;
                color: #f8fafc;
                selection-background-color: #2563eb;
                selection-color: #ffffff;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                outline: 0;
            }}
        """)
        if self.cb_serial_baud.lineEdit():
            self.cb_serial_baud.lineEdit().setStyleSheet("""
                QLineEdit {
                    background: transparent;
                    color: #f8fafc;
                    border: none;
                    padding: 0px;
                }
            """)
        self.cb_serial_baud.currentTextChanged.connect(self.on_serial_device_changed)
        layout.addWidget(self.cb_serial_baud)

        # Refresh Ports Button
        self.btn_refresh_ports = QPushButton("🔄")
        self.btn_refresh_ports.setToolTip(language.tr("tooltip_refresh_ports"))
        self.btn_refresh_ports.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 8px;
                font-size: 13px;
            }
            QPushButton:hover { background-color: rgba(56, 189, 248, 0.2); border-color: #38bdf8; color: white; }
        """)
        self.btn_refresh_ports.clicked.connect(self.refresh_serial_ports)
        layout.addWidget(self.btn_refresh_ports)

        # Test Connection Button
        self.btn_test_serial = QPushButton(f"{language.tr('btn_test_conn')}")
        self.btn_test_serial.setToolTip(language.tr("tooltip_test_conn"))
        self.btn_test_serial.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #cbd5e1;
                border: 1px solid rgba(255, 255, 255, 0.15);
                border-radius: 6px;
                padding: 4px 10px;
                font-size: 12px;
            }
            QPushButton:hover { background-color: rgba(16, 185, 129, 0.2); border-color: #10b981; color: #6ee7b7; }
        """)
        self.btn_test_serial.clicked.connect(self.test_serial_connection)
        layout.addWidget(self.btn_test_serial)

        # Status Badge Pill
        self.lbl_serial_status = QLabel(f"● {language.tr('status_port_unselected')}")
        self.lbl_serial_status.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; margin-left: 4px;")
        layout.addWidget(self.lbl_serial_status)

        layout.addStretch(1)

        # Quick Terminal Button
        self.btn_quick_terminal = QPushButton("📟 " + language.tr("terminal_title", default="Serial Terminal"))
        self.btn_quick_terminal.setToolTip("Open interactive serial terminal for this port")
        self.btn_quick_terminal.setStyleSheet("""
            QPushButton {
                background-color: rgba(6, 182, 212, 0.15);
                color: #67e8f9;
                border: 1px solid rgba(6, 182, 212, 0.35);
                border-radius: 6px;
                padding: 4px 12px;
                font-size: 12px;
                font-weight: bold;
            }
            QPushButton:hover {
                background-color: rgba(6, 182, 212, 0.3);
                border-color: #06b6d4;
                color: white;
            }
        """)
        self.btn_quick_terminal.clicked.connect(self.open_global_serial_terminal)
        layout.addWidget(self.btn_quick_terminal)

        self.refresh_serial_ports()
        return container

    def refresh_serial_ports(self):
        """Rescan available USB serial ports and update dropdown list."""
        current = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        from cards.serial_session import serial_registry
        ports = serial_registry.available_ports()

        self.cb_serial_port.blockSignals(True)
        self.cb_serial_port.clear()
        for p in ports:
            self.cb_serial_port.addItem(p)

        if current and current in ports:
            self.cb_serial_port.setCurrentText(current)
        elif ports:
            self.cb_serial_port.setCurrentIndex(0)
        else:
            self.cb_serial_port.setEditText("")

        self.cb_serial_port.blockSignals(False)
        self.on_serial_device_changed()

        if hasattr(self, "live_console"):
            if ports:
                self.live_console.append_log(
                    f"[SERIAL] 🔄 {len(ports)} serial port(s) detected: {', '.join(ports)}"
                )
            else:
                self.live_console.append_log("[SERIAL] ⚠️ No serial ports detected.")

    def apply_global_serial_settings(self, port: str, baudrate: int = 115200):
        """Apply restored serial port and baudrate to UI and registry."""
        port = str(port or "").strip()
        baudrate = int(baudrate) if baudrate > 0 else 115200

        self.cb_serial_port.blockSignals(True)
        if port:
            if self.cb_serial_port.findText(port) < 0:
                self.cb_serial_port.insertItem(0, port)
            self.cb_serial_port.setCurrentText(port)
        self.cb_serial_port.blockSignals(False)

        self.cb_serial_baud.blockSignals(True)
        self.cb_serial_baud.setCurrentText(str(baudrate))
        self.cb_serial_baud.blockSignals(False)

        self.on_serial_device_changed()

    def on_serial_device_changed(self):
        """Update serial_registry and card badges whenever global serial port or baudrate changes."""
        port = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        try:
            baud = int(self.cb_serial_baud.currentText()) if hasattr(self, "cb_serial_baud") else 115200
        except ValueError:
            baud = 115200

        from cards.serial_session import serial_registry
        if port:
            serial_registry.set_default(port=port, baudrate=baud)
            if port in serial_registry.available_ports():
                self.lbl_serial_status.setText(f"● {language.tr('status_port_connected')}")
                self.lbl_serial_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold; margin-left: 4px;")
            else:
                self.lbl_serial_status.setText(f"● {language.tr('status_port_error')}")
                self.lbl_serial_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; margin-left: 4px;")
        else:
            self.lbl_serial_status.setText(f"● {language.tr('status_port_unselected')}")
            self.lbl_serial_status.setStyleSheet("color: #64748b; font-size: 11px; font-weight: bold; margin-left: 4px;")

        # Update badges on all cards
        if hasattr(self, "drop_zone"):
            for card_w in self.drop_zone.cards.values():
                card_w.update_style()
        if hasattr(self, "refresh_dependency_badges"):
            self.refresh_dependency_badges()

    def test_serial_connection(self):
        """Probe the currently selected serial port and show result in status pill and live console."""
        port = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        try:
            baud = int(self.cb_serial_baud.currentText()) if hasattr(self, "cb_serial_baud") else 115200
        except ValueError:
            baud = 115200

        if not port:
            self.lbl_serial_status.setText(f"● {language.tr('status_port_unselected')}")
            self.lbl_serial_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; margin-left: 4px;")
            if hasattr(self, "live_console"):
                self.live_console.append_log(f"[SERIAL] ⚠️ {language.tr('status_port_unselected')}")
            return

        from cards.serial_session import serial_registry
        ok, msg = serial_registry.probe(port, baud)
        if ok:
            self.lbl_serial_status.setText(f"● {language.tr('status_port_connected')}")
            self.lbl_serial_status.setStyleSheet("color: #10b981; font-size: 11px; font-weight: bold; margin-left: 4px;")
            if hasattr(self, "live_console"):
                self.live_console.append_log(f"[SERIAL] ✅ {port} @ {baud} bps: {msg}")
        else:
            self.lbl_serial_status.setText(f"● {language.tr('status_port_error')}")
            self.lbl_serial_status.setStyleSheet("color: #ef4444; font-size: 11px; font-weight: bold; margin-left: 4px;")
            if hasattr(self, "live_console"):
                self.live_console.append_log(f"[SERIAL] ❌ {port} @ {baud} bps: {msg}")

    def open_global_serial_terminal(self):
        """Open the interactive serial terminal modal for the currently selected port."""
        port = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        try:
            baud = int(self.cb_serial_baud.currentText()) if hasattr(self, "cb_serial_baud") else 115200
        except ValueError:
            baud = 115200

        from ui_qt.serial_terminal_dialog import SerialTerminalDialog
        dialog = SerialTerminalDialog({"port": port, "baudrate": baud}, parent=self)
        dialog.exec()

    def retranslate_device_toolbar(self):
        """Retranslate strings on the global device toolbar."""
        if hasattr(self, "lbl_dev_title"):
            self.lbl_dev_title.setText(language.tr("lbl_dut_port"))
        if hasattr(self, "lbl_dev_baud"):
            self.lbl_dev_baud.setText(language.tr("lbl_baudrate"))
        if hasattr(self, "btn_refresh_ports"):
            self.btn_refresh_ports.setToolTip(language.tr("tooltip_refresh_ports"))
        if hasattr(self, "btn_test_serial"):
            self.btn_test_serial.setText(f"{language.tr('btn_test_conn')}")
            self.btn_test_serial.setToolTip(language.tr("tooltip_test_conn"))
        if hasattr(self, "btn_quick_terminal"):
            self.btn_quick_terminal.setText("📟 " + language.tr("terminal_title", default="Serial Terminal"))
        self.on_serial_device_changed()

    def retranslate_status_bar(self):
        """The bottom status bar. Leaves the progress text alone while running."""
        self.lbl_station_info.setText(language.tr(
            "status_station_info", station=self.station_name, operator=self.operator_name
        ))
        self.lbl_station_info.setToolTip(
            language.tr("tooltip_trace_path", path=self.trace_log.jsonl_path)
        )
        if not self.is_running_all:
            self.reset_progress_display()

    def on_btn_run_all_clicked(self):
        """Toggles between Run All and Stop Batch Test Execution"""
        if self.is_running_all:
            self.request_stop()
        else:
            self.run_all_cards()

    def request_stop(self):
        """
        Ask the running work to stop, and tell it so.

        Pressing Stop used to only set `stop_requested`, a flag nothing ever
        read - the sequence thread's own cancel flag stayed false, so
        `is_cancelled()` never returned True and the run continued to the end.
        A step waiting 20 s for serial data ignored the button entirely.

        Cancellation is cooperative: the thread's flag is read by the sequence
        between steps and by the modules inside their own waits, so a step stops
        within about a second rather than instantly. The button is disabled here
        so the operator can see the press registered while that plays out.
        """
        self.stop_requested = True

        thread = getattr(self, "sequence_thread", None)
        if thread is not None and thread.isRunning():
            thread.cancel()

        # A card started on its own is not part of the sequence thread, so it
        # needs telling separately.
        for card in self.drop_zone.cards.values():
            runner = getattr(card, "runner_thread", None)
            if runner is not None and runner.isRunning():
                runner.cancel()

        self.btn_run_all.setEnabled(False)
        self.set_progress_text(language.tr("status_stopping"))

    def set_run_all_button_mode(self, is_stop_mode: bool):
        """Dynamic styling and label toggle for Run All / Stop button"""
        # request_stop() disables the button so a press is visibly registered.
        # Every path that leaves the running state comes through here, so this
        # is the one place that has to hand it back - otherwise a stopped run
        # leaves the operator with a dead button.
        self.btn_run_all.setEnabled(True)
        self.btn_clear_results_bar.setEnabled(not is_stop_mode)
        if is_stop_mode:
            self.btn_run_all.setText(language.tr("btn_stop_all"))
            self.btn_run_all.setStyleSheet("""
                QPushButton {
                    background-color: #ef4444;
                    color: white;
                    font-weight: bold;
                    padding: 8px 18px;
                    border-radius: 8px;
                    border: 1px solid #f87171;
                }
                QPushButton:hover {
                    background-color: #dc2626;
                }
                QPushButton:pressed {
                    background-color: #b91c1c;
                    border-color: #fca5a5;
                    padding: 9px 17px 7px 19px;
                }
            """)
        else:
            self.btn_run_all.setText(language.tr("btn_run_all"))
            self.btn_run_all.setStyleSheet("""
                QPushButton {
                    background-color: #3b82f6;
                    color: white;
                    font-weight: bold;
                    padding: 8px 18px;
                    border-radius: 8px;
                    border: 1px solid #60a5fa;
                }
                QPushButton:hover {
                    background-color: #2563eb;
                }
                QPushButton:pressed {
                    background-color: #1d4ed8;
                    border-color: #93c5fd;
                    padding: 9px 17px 7px 19px;
                }
            """)

    def clear_all_results(self):
        """Resets status, execution time, and summary text of all dashboard cards to initial PENDING state"""
        if self.is_running_all:
            return
        for card_id, card_w in self.drop_zone.cards.items():
            if card_w.module_type == "test":
                card_w.status = "PENDING"
                card_w.execution_time = "--"
                card_w.summary_text = language.tr("status_pending")
                card_w.last_result = None
                card_w.iteration = 0
                card_w.iterations_total = 0
                card_w.update_style()
        self.last_report_data = None
        self.reset_progress_display()
        self.statusBar().showMessage(language.tr("status_idle"), 3000)

    def config_card_count(self) -> int:
        """
        How many config cards sit at the top of the dashboard.

        There is one per shared instrument - the serial session and the PPK2
        session - so this is a count, not a boolean. Everything that used to
        treat "index 0" as "the config card" uses this instead.
        """
        return sum(1 for c in self.drop_zone.cards.values()
                   if c.module.module_type == "config")

    def get_serial_config(self) -> dict:
        """Return the current global serial configuration from the device toolbar."""
        port = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        try:
            baud = int(self.cb_serial_baud.currentText()) if hasattr(self, "cb_serial_baud") else 115200
        except ValueError:
            baud = 115200
        return {"port": port, "baudrate": baud}

    def populate_palette(self):
        self.palette_list.clear()
        sorted_modules = sorted(
            LOADED_MODULES.values(),
            key=lambda m: (0 if getattr(m, "module_type", "test") == "config" else 1, m.get_localized("name", language.current_lang))
        )
        lang = language.current_lang
        use_mock = self.mock_checkbox.isChecked() if hasattr(self, "mock_checkbox") else False
        for mod_inst in sorted_modules:
            emoji = get_module_emoji(mod_inst.icon, mod_inst)
            name = mod_inst.get_localized("name", lang)
            desc = mod_inst.get_localized("description", lang)
            ver = f"v{getattr(mod_inst, 'version', '1.0.0')}"
            is_ready = getattr(mod_inst, "is_ready", True)

            wip_tag = ""
            if not is_ready:
                wip_tag = f" [{language.tr('badge_wip_mock_active')}]" if use_mock else f" [{language.tr('badge_wip_disabled')}]"

            display_name = f"{emoji}  {name}  {ver}{wip_tag}"
            # The plain text stays as the accessible/searchable form; the
            # delegate renders from these roles so the name can be bold.
            item = QListWidgetItem(f"{display_name}\n   {desc}")
            item.setData(Qt.UserRole, mod_inst.module_id)
            item.setData(PALETTE_NAME_ROLE, display_name)
            item.setData(PALETTE_DESC_ROLE, desc)
            # The module's own accent colour, lifted to be readable on the
            # item background - see legible_on().
            item.setData(PALETTE_COLOR_ROLE, legible_on(
                getattr(mod_inst, "color", None) or ModuleItemDelegate.NAME_COLOR,
                ModuleItemDelegate.ITEM_BACKGROUND,
            ))

            # Tags are searchable but would crowd the two-line item, so they go
            # in the tooltip where they can be read on demand.
            tags = mod_inst.localized_tags(lang)
            tooltip = [f"{name} ({ver})", f"{desc}", "", f"ID: {mod_inst.module_id}"]
            if not is_ready:
                tooltip.append(f"⚠️ {language.tr('tooltip_wip_card_disabled') if not use_mock else language.tr('tooltip_wip_mock_active')}")
            if tags:
                tooltip.append(language.tr("palette_tags", tags=", ".join(tags)))
            item.setToolTip("\n".join(tooltip))

            self.palette_list.addItem(item)

        self.palette_list.refresh_item_heights()

        # Keep an active query applied across repopulation (language change,
        # module reload), otherwise the list silently jumps back to everything.
        self.apply_palette_filter()

    def apply_palette_filter(self):
        """
        Hide palette entries that do not match the search box.

        Filtering by hiding rather than rebuilding keeps drag-and-drop and the
        UserRole module_id intact, and makes clearing the box instant.
        """
        if not hasattr(self, "palette_search") or not hasattr(self, "palette_list"):
            return

        query = self.palette_search.text().strip()
        lang = language.current_lang
        total = self.palette_list.count()
        shown = 0

        for row in range(total):
            item = self.palette_list.item(row)
            module = get_module(LOADED_MODULES, item.data(Qt.UserRole))
            visible = True if module is None else module.matches_search(query, lang)
            item.setHidden(not visible)
            shown += 1 if visible else 0

        if not query:
            self.palette_result_label.setVisible(False)
        elif shown == 0:
            self.palette_result_label.setText(language.tr("palette_no_match", query=query))
            self.palette_result_label.setVisible(True)
        else:
            self.palette_result_label.setText(
                language.tr("palette_match_count", shown=shown, total=total))
            self.palette_result_label.setVisible(True)

    def reload_cards_ui(self):
        load_test_cards()
        self.update_language_menu()
        self.populate_palette()
        QMessageBox.information(self, language.tr("btn_reload"), language.tr("msg_reload_complete", count=len(LOADED_CARDS)))

    reload_modules_ui = reload_cards_ui

    def ensure_config_cards_at_top(self):
        cards_dict = self.drop_zone.cards
        if not cards_dict:
            return

        card_keys = list(cards_dict.keys())

        # In Operator Mode, if dut_serialnumber_reader is present, it must be the very first card
        reader_key = None
        if hasattr(self, "mode_controller") and getattr(self.mode_controller, "mode", None) is AppMode.OPERATOR:
            for cid in card_keys:
                if getattr(cards_dict[cid].module, "module_id", "") == "dut_serialnumber_reader":
                    reader_key = cid
                    break

        config_keys = [cid for cid in card_keys
                       if cid != reader_key and getattr(cards_dict[cid].card, "card_type", getattr(cards_dict[cid].module, "module_type", "test")) == "config"]
        # Sort config cards by their priority (e.g. PPK/Power=10, Serial=20, others=30)
        config_keys.sort(key=lambda cid: (
            getattr(cards_dict[cid].card, "priority", 50),
            card_keys.index(cid),
        ))
        
        remaining = [cid for cid in card_keys if cid not in config_keys and cid != reader_key]
        reordered = ([reader_key] if reader_key else []) + config_keys + remaining
        if reordered != card_keys:
            self.drop_zone.cards = {k: cards_dict[k] for k in reordered}

        self.drop_zone.rebuild_layout()

    ensure_serial_card_at_top = ensure_config_cards_at_top

    def load_dashboard_state(self):
        layout_data = None
        if os.path.exists(LAYOUT_FILE):
            try:
                with open(LAYOUT_FILE, "r", encoding="utf-8") as f:
                    layout_data = json.load(f)
            except Exception as e:
                print(f"⚠️ Failed to read layout file: {e}")

        if layout_data and isinstance(layout_data, dict):
            # 1. Restore application-wide settings
            settings = layout_data.get("settings", {})
            if isinstance(settings, dict) and settings:
                saved_lang = settings.get("language")
                if saved_lang and saved_lang in language.discover_languages(LOADED_MODULES):
                    if saved_lang != language.current_lang:
                        language.set_language(saved_lang)
                        self.retranslate_ui()

                if "mock_mode" in settings and hasattr(self, "mock_checkbox"):
                    self.mock_checkbox.blockSignals(True)
                    self.mock_checkbox.setChecked(bool(settings["mock_mode"]))
                    self.mock_checkbox.blockSignals(False)

                if "auto_report" in settings and hasattr(self, "auto_report_checkbox"):
                    self.auto_report_checkbox.blockSignals(True)
                    self.auto_report_checkbox.setChecked(bool(settings["auto_report"]))
                    self.auto_report_checkbox.blockSignals(False)

                if "stop_on_fail" in settings and hasattr(self, "act_stop_on_fail"):
                    self.stop_on_fail = bool(settings["stop_on_fail"])
                    self.act_stop_on_fail.blockSignals(True)
                    self.act_stop_on_fail.setChecked(self.stop_on_fail)
                    self.act_stop_on_fail.blockSignals(False)

                if "stop_batch_on_fail" in settings and hasattr(self, "act_stop_batch_on_fail"):
                    self.stop_batch_on_fail = bool(settings["stop_batch_on_fail"])
                    self.act_stop_batch_on_fail.blockSignals(True)
                    self.act_stop_batch_on_fail.setChecked(self.stop_batch_on_fail)
                    self.act_stop_batch_on_fail.blockSignals(False)

                if "seq_repeat" in settings and hasattr(self, "spin_seq_repeat"):
                    try:
                        self.spin_seq_repeat.blockSignals(True)
                        self.spin_seq_repeat.setValue(int(settings["seq_repeat"]))
                        self.spin_seq_repeat.blockSignals(False)
                    except (ValueError, TypeError):
                        pass

                if "mode" in settings and hasattr(self, "mode_controller"):
                    target_mode = str(settings["mode"]).lower()
                    if target_mode == "operator" and self.mode_controller.mode is not AppMode.OPERATOR:
                        self.mode_controller.to_operator()
                        self.apply_mode_policy()
                    elif target_mode == "engineer" and self.mode_controller.mode is not AppMode.ENGINEER:
                        self.mode_controller.to_engineer()
                        self.apply_mode_policy()

                if "console_collapsed" in settings and hasattr(self, "live_console"):
                    if bool(settings["console_collapsed"]) != self.live_console._collapsed:
                        self.live_console.toggle_collapse()

                w = settings.get("window_width")
                h = settings.get("window_height")
                if isinstance(w, int) and isinstance(h, int) and w >= 800 and h >= 600:
                    self.resize(w, h)

            # 2. Restore hardware device settings
            dev_settings = layout_data.get("device_settings", {})
            if isinstance(dev_settings, dict) and hasattr(self, "cb_serial_port"):
                saved_port = dev_settings.get("serial_port", "")
                saved_baud = dev_settings.get("serial_baudrate", 115200)
                self.apply_global_serial_settings(saved_port, saved_baud)

            # 3. Restore cards
            cards = layout_data.get("cards", [])
            if cards and len(cards) > 0:
                for item in cards:
                    module = get_module(LOADED_MODULES, item["module_id"])
                    if module is not None:
                        card_id = item.get("card_id", f"card_{random.randint(1000, 9999)}")
                        initial_crit = item.get("criteria", None)
                        card_widget = TestCardWidget(card_id, module, initial_crit)
                        card_widget.card_clicked.connect(self.on_card_clicked)
                        self.drop_zone.add_card(card_widget)

        self.ensure_serial_card_at_top()

    def save_dashboard_state(self):
        self.ensure_serial_card_at_top()
        cards_list = []
        for card_id, card_widget in self.drop_zone.cards.items():
            cards_list.append({
                "card_id": card_id,
                "module_id": card_widget.module.module_id,
                "criteria": card_widget.criteria
            })

        settings = {
            "language": language.current_lang,
            "mock_mode": self.mock_checkbox.isChecked() if hasattr(self, "mock_checkbox") else False,
            "auto_report": self.auto_report_checkbox.isChecked() if hasattr(self, "auto_report_checkbox") else True,
            "stop_on_fail": getattr(self, "stop_on_fail", False),
            "stop_batch_on_fail": getattr(self, "stop_batch_on_fail", False),
            "seq_repeat": self.spin_seq_repeat.value() if hasattr(self, "spin_seq_repeat") else 1,
            "mode": self.mode_controller.mode.value if hasattr(self, "mode_controller") else "engineer",
            "console_collapsed": getattr(self.live_console, "_collapsed", False) if hasattr(self, "live_console") else False,
            "window_width": self.width(),
            "window_height": self.height(),
        }

        serial_port = self.cb_serial_port.currentText().strip() if hasattr(self, "cb_serial_port") else ""
        try:
            serial_baud = int(self.cb_serial_baud.currentText()) if hasattr(self, "cb_serial_baud") else 115200
        except ValueError:
            serial_baud = 115200

        data = {
            "settings": settings,
            "device_settings": {
                "serial_port": serial_port,
                "serial_baudrate": serial_baud,
            },
            "cards": cards_list
        }
        try:
            with open(LAYOUT_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"⚠️ Failed to save layout state: {e}")

    def closeEvent(self, event):
        self.save_dashboard_state()

        # Safely shut down sequence runner thread if active
        if hasattr(self, "sequence_thread") and self.sequence_thread and self.sequence_thread.isRunning():
            try:
                self.sequence_thread.cancel()
                self.sequence_thread.quit()
                self.sequence_thread.wait(1500)
            except Exception:
                pass

        # Safely shut down individual card runner threads if active
        if hasattr(self, "drop_zone") and hasattr(self.drop_zone, "cards"):
            for card_w in list(self.drop_zone.cards.values()):
                if hasattr(card_w, "runner_thread") and card_w.runner_thread and card_w.runner_thread.isRunning():
                    try:
                        card_w.runner_thread.cancel()
                        card_w.runner_thread.quit()
                        card_w.runner_thread.wait(1000)
                    except Exception:
                        pass

        super().closeEvent(event)

    def add_card_to_dashboard(self, module_id: str, insert_idx: int = -1, check_deps: bool = True):
        mod_inst = get_module(LOADED_MODULES, module_id)
        if mod_inst is None:
            return
        module_id = mod_inst.module_id   # normalise an alias to the current id

        # Prerequisite check: report a missing one now rather than at run time.
        # DTM, for instance, uses the shared serial session, so the Serial
        # Interface Config card has to be there first; added without it, the run
        # fails later with "no serial port configured".
        if check_deps and not self._ensure_dependencies(module_id):
            return

        # A config card is allowed once **per config module**, not once in total.
        # Each one owns a different shared instrument - the serial session and
        # the PPK2 session - so a sequence measuring current over DTM needs both
        # cards. The limit that matters is that no instrument gets two owners.
        config_cards = [c for c in self.drop_zone.cards.values()
                        if c.module.module_type == "config"]
        incoming_is_config = mod_inst.module_type == "config"
        if incoming_is_config:
            for existing_card in config_cards:
                if existing_card.module.module_id == module_id:
                    QMessageBox.warning(
                        self,
                        language.tr("dep_error_title"),
                        language.tr("msg_config_card_limit", module=mod_inst.get_localized(
                            "name", language.current_lang) or module_id),
                    )
                    return
            # Config cards stay above the test cards, in the order they were
            # added: a session has to be open before anything borrows it.
            insert_idx = len(config_cards)
        else:
            # A test card must not land above a config card (except dut_serialnumber_reader in operator mode)
            is_op_reader = module_id == "dut_serialnumber_reader" and getattr(self.mode_controller, "mode", None) is AppMode.OPERATOR
            if insert_idx >= 0 and insert_idx < len(config_cards) and not is_op_reader:
                insert_idx = len(config_cards)

        card_id = f"card_{random.randint(1000, 9999)}"
        
        card_widget = TestCardWidget(card_id, mod_inst)
        card_widget.card_clicked.connect(self.on_card_clicked)
        self.drop_zone.add_card(card_widget, insert_idx=insert_idx)
        self.ensure_serial_card_at_top()
        self.save_dashboard_state()
        self.refresh_dependency_badges()
        self.reset_progress_display()

    def _present_card_ids(self) -> List[str]:
        if not hasattr(self, "drop_zone") or self.drop_zone is None:
            return []
        return [cw.card.card_id for cw in self.drop_zone.cards.values()]

    _present_module_ids = _present_card_ids

    def _ensure_dependencies(self, card_id: str) -> bool:
        """
        Report a missing prerequisite with an explanation, and offer to add it
        when that resolves the problem.
        :return: whether the card may be added
        """
        card = get_card(LOADED_CARDS, card_id)
        if card is None:
            return False
        report = check_dependencies(card, self._present_card_ids(), LOADED_CARDS)
        if report.ok:
            return True

        card_name = card.get_localized("name", language.current_lang) or card_id
        reasons = report.describe()

        if not report.fixable:
            # The requirement names a card that is not installed - the user
            # has to add the file.
            QMessageBox.warning(
                self, language.tr("dep_error_title"),
                language.tr("dep_missing_body_no_fix", module=card_name, reasons=reasons),
            )
            return False

        # Dynamic prompt from report (decoupled from hardcoded instruments)
        dialog_title, dialog_text = report.get_prompt(card_name)

        box = QMessageBox(self)
        box.setIcon(QMessageBox.Question)
        box.setWindowTitle(dialog_title)
        box.setText(dialog_title)
        box.setInformativeText(dialog_text)
        btn_add = box.addButton(language.tr("btn_add_card"), QMessageBox.AcceptRole)
        box.addButton(language.tr("btn_cancel"), QMessageBox.RejectRole)
        box.exec()

        if box.clickedButton() is not btn_add:
            return False

        added = []
        for dep_id in report.cards_to_add():
            if get_card(LOADED_CARDS, dep_id) is not None:
                # The prerequisite itself is not checked recursively; that
                # could form a cycle.
                self.add_card_to_dashboard(dep_id, check_deps=False)
                dep_card = get_card(LOADED_CARDS, dep_id)
                dep_name = dep_card.get_localized("name", language.current_lang) if dep_card else dep_id
                added.append(dep_name)

        if added:
            self.ensure_config_cards_at_top()
            self.statusBar().showMessage(language.tr("dep_added", modules=", ".join(added)), 8000)

        # Re-check after adding. Still unmet means do not proceed.
        return check_dependencies(card, self._present_card_ids(), LOADED_CARDS).ok

    def remove_card(self, card_id: str):
        card = self.drop_zone.cards.get(card_id)
        if card is not None:
            # Check whether removing this card would make others unrunnable
            # (removing Serial Config breaks DTM and LFCLK, for example).
            dependents = find_dependents(
                card.module.module_id, self._present_module_ids(), LOADED_MODULES
            )
            if dependents:
                names = "\n".join(
                    f"• {get_module(LOADED_MODULES, mid).get_localized('name', language.current_lang) or mid}"
                    for mid in dependents if get_module(LOADED_MODULES, mid) is not None
                )
                box = QMessageBox(self)
                box.setIcon(QMessageBox.Warning)
                box.setWindowTitle(language.tr("dep_removal_title"))
                box.setText(language.tr(
                    "dep_removal_body",
                    module=card.module.get_localized("name", language.current_lang) or card.module.module_id,
                    dependents=names,
                ))
                btn_remove = box.addButton(language.tr("btn_remove_anyway"), QMessageBox.DestructiveRole)
                box.addButton(language.tr("btn_cancel"), QMessageBox.RejectRole)
                box.exec()
                if box.clickedButton() is not btn_remove:
                    return

        self.drop_zone.remove_card(card_id)
        self.save_dashboard_state()
        self.refresh_dependency_badges()
        self.reset_progress_display()

    def refresh_dependency_badges(self) -> None:
        """Refresh each dashboard card's indication of whether it can run."""
        if not hasattr(self, "drop_zone") or self.drop_zone is None:
            return
        present = self._present_module_ids()
        for card in self.drop_zone.cards.values():
            # The card's criteria are known here, so a port override clears the
            # serial prerequisite. At drop time they are not, and the check
            # stays strict on purpose.
            report = check_dependencies(card.module, present, LOADED_MODULES, card.criteria)
            card.set_dependency_warning("" if report.ok else report.short())

    def move_card(self, card_id: str, delta: int):
        cards_dict = self.drop_zone.cards
        card_keys = list(cards_dict.keys())
        if card_id not in card_keys:
            return
            
        if cards_dict[card_id].module.module_type == "config":
            return

        idx = card_keys.index(card_id)
        new_idx = idx + delta
        
        # Test cards must stay below the config block, however many cards it has.
        config_count = sum(1 for k in card_keys
                           if cards_dict[k].module.module_type == "config")
        if new_idx < config_count:
            return

        if 0 <= new_idx < len(card_keys):
            card_keys[idx], card_keys[new_idx] = card_keys[new_idx], card_keys[idx]
            new_cards = {k: cards_dict[k] for k in card_keys}
            self.drop_zone.cards = new_cards
            self.drop_zone.rebuild_layout()
            self.save_dashboard_state()

    def reorder_card_to_index(self, card_id: str, target_idx: int):
        cards_dict = self.drop_zone.cards
        card_keys = list(cards_dict.keys())
        if card_id not in card_keys:
            return
        
        dragged_card = cards_dict[card_id]
        if dragged_card.module.module_type == "config":
            return

        config_count = sum(1 for k in card_keys
                           if cards_dict[k].module.module_type == "config")
        target_idx = max(config_count, target_idx)

        current_idx = card_keys.index(card_id)
        card_keys.pop(current_idx)
        
        if target_idx > len(card_keys):
            target_idx = len(card_keys)
            
        card_keys.insert(target_idx, card_id)

        new_cards = {k: cards_dict[k] for k in card_keys}
        self.drop_zone.cards = new_cards
        self.drop_zone.rebuild_layout()
        self.save_dashboard_state()

    def _register_serial_target(self):
        """Register the port chosen by the session owner (the Serial Interface
        card) as the sequence's default target."""
        cfg = self.get_serial_config()
        if cfg and str(cfg.get("port", "")).strip():
            register_serial_target(cfg)

    def _now_iso(self) -> str:
        return datetime.datetime.now().isoformat(timespec="seconds")

    @staticmethod
    def _today_start() -> str:
        """Today at 00:00:00 as an ISO string, compared textually against the
        trace records' started_at."""
        return datetime.date.today().isoformat() + "T00:00:00"

    def reset_yield_counters(self) -> None:
        """
        Clear only the on-screen yield counters. The trace log is append-only and
        is left untouched. Used when starting a new lot.
        """
        self.yield_scope_since = self._now_iso()
        self.yield_stats = YieldStats()
        self.update_yield_display()
        self.statusBar().showMessage(
            language.tr("msg_yield_reset", scope=self.yield_scope_label), 8000
        )

    # ==========================================================================
    # Recipe - a versioned sequence. Lock / save / import / export.
    # ==========================================================================
    def build_recipe_from_cards(self) -> Recipe:
        """
        Turn the current dashboard into a recipe. step_id reuses card_id so that
        progress signals can be routed back to the right card.
        """
        use_mock = self.mock_checkbox.isChecked() if hasattr(self, "mock_checkbox") else False
        cards = [(cw.module.module_id, dict(cw.criteria)) for cw in self.drop_zone.cards.values()]
        recipe = recipe_from_cards(
            cards, LOADED_MODULES,
            name=self.recipe.name, version=self.recipe.version,
            timestamp=self._now_iso(), author=self.operator_name,
        )
        recipe.locked = self.recipe.locked
        recipe.notes = self.recipe.notes
        recipe.created_at = self.recipe.created_at or recipe.created_at
        recipe.created_by = self.recipe.created_by or recipe.created_by

        # Align step_id with card_id and disable unready steps in hardware mode.
        for step, card_widget in zip(recipe.steps, self.drop_zone.cards.values()):
            step.step_id = card_widget.card_id
            if not getattr(card_widget.module, "is_ready", True) and not use_mock:
                step.enabled = False
        return recipe

    def apply_recipe_to_dashboard(self, recipe: Recipe) -> None:
        """Rebuild the dashboard from the recipe's steps."""
        for card_id in list(self.drop_zone.cards.keys()):
            self.drop_zone.remove_card(card_id)

        for step in recipe.steps:
            module = get_module(LOADED_MODULES, step.module_id)
            if module is None:
                continue
            card = TestCardWidget(step.step_id or f"card_{random.randint(1000, 9999)}",
                                  module, dict(step.criteria), self.drop_zone)
            card.card_clicked.connect(self.on_card_clicked)
            self.drop_zone.add_card(card)

        self.recipe = recipe
        self.update_recipe_display()
        self.apply_mode_policy()
        self.refresh_dependency_badges()
        self.reset_progress_display()
        self.save_dashboard_state()

    def update_recipe_display(self) -> None:
        lock = " " + language.tr("recipe_locked_badge") if self.recipe.locked else ""
        fp = self.build_recipe_from_cards().short_fingerprint if self.drop_zone.cards else "-"
        self.recipe_label.setText(f"📋 {self.recipe.label}{lock}")
        self.recipe_label.setToolTip(language.tr(
            "recipe_tooltip_summary",
            count=len(self.drop_zone.cards), fp=fp,
            notes=self.recipe.notes or language.tr("recipe_no_notes"),
        ))
        self.recipe_fp_label.setText(fp)

    def on_recipe_info_clicked(self) -> None:
        recipe = self.build_recipe_from_cards()
        recipe.locked = self.recipe.locked
        recipe.notes = self.recipe.notes
        allow_lock = self.mode_controller.policy.can_edit_recipe
        dlg = RecipeInfoDialog(recipe, allow_lock_change=allow_lock, parent=self)
        if dlg.exec() == QDialog.Accepted:
            dlg.apply_to(self.recipe)
            self.update_recipe_display()
            self.apply_mode_policy()

    def on_import_recipe_clicked(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, language.tr("recipe_import_title"), "",
            f"Recipe (*.recipe.json *.json);;{language.tr('filter_all_files')} (*)"
        )
        if not path:
            return

        recipe, report = import_recipe(path, LOADED_MODULES)
        dlg = ImportReportDialog(recipe, report, path, parent=self)
        if dlg.exec() == QDialog.Accepted and recipe is not None:
            self.apply_recipe_to_dashboard(recipe)
            self.statusBar().showMessage(
                language.tr("recipe_applied", label=recipe.label, fp=recipe.short_fingerprint), 10000
            )

    def on_export_recipe_clicked(self) -> None:
        recipe = self.build_recipe_from_cards()
        recipe.locked = self.recipe.locked
        recipe.notes = self.recipe.notes

        dlg = RecipeInfoDialog(recipe, allow_lock_change=True, parent=self)
        if dlg.exec() != QDialog.Accepted:
            return
        dlg.apply_to(recipe)
        dlg.apply_to(self.recipe)

        default_name = f"{recipe.name.replace(' ', '_')}_v{recipe.version}{RECIPE_FILE_SUFFIX}"
        path, _ = QFileDialog.getSaveFileName(
            self, language.tr("recipe_export_title"), default_name,
            f"Recipe (*.recipe.json);;{language.tr('filter_all_files')} (*)"
        )
        if not path:
            return
        try:
            saved = recipe.export(path, timestamp=self._now_iso(), station=self.station_name)
        except Exception as e:
            QMessageBox.warning(self, language.tr("recipe_export_failed_title"),
                                language.tr("recipe_export_failed_body", error=e))
            return

        self.update_recipe_display()
        QMessageBox.information(
            self, language.tr("recipe_export_title"),
            language.tr("recipe_export_done", path=saved, label=recipe.label,
                    count=len(recipe.steps), fp=recipe.short_fingerprint),
        )

    # ==========================================================================
    # Operator / engineer mode
    # ==========================================================================
    def on_mode_toggle_clicked(self) -> None:
        if self.mode_controller.mode is AppMode.ENGINEER:
            self.mode_controller.to_operator()
            self.apply_mode_policy()
            self.save_dashboard_state()
            return

        if self.mode_controller.is_protected:
            dlg = PasscodeDialog(self)
            if dlg.exec() != QDialog.Accepted:
                return
            if not self.mode_controller.to_engineer(dlg.passcode):
                QMessageBox.warning(self, language.tr("msg_engineer_mode_title"),
                                    language.tr("msg_wrong_passcode"))
                return
        else:
            self.mode_controller.to_engineer()
        self.apply_mode_policy()
        self.save_dashboard_state()

    def apply_mode_policy(self) -> None:
        """
        Apply the mode policy to the UI. The policy decisions live in core.modes;
        this only hides or disables widgets.
        """
        policy = self.mode_controller.policy
        mode = self.mode_controller.mode
        locked_recipe = self.recipe.locked and mode is AppMode.OPERATOR
        can_edit = policy.can_edit_criteria and not locked_recipe

        self.btn_mode.setText(f"{mode.badge} {mode.display_name}")
        self.btn_mode.setStyleSheet(
            MODE_BTN_OPERATOR_STYLE if mode is AppMode.OPERATOR else MODE_BTN_ENGINEER_STYLE
        )

        self.palette_container.setVisible(policy.shows_palette)
        self.mock_checkbox.setEnabled(policy.can_toggle_mock)
        if policy.forces_real_hardware:
            self.mock_checkbox.setChecked(False)
        self.btn_reload.setVisible(policy.can_reload_modules)
        self.btn_export_recipe.setVisible(policy.can_export_recipe)
        self.yield_container.setVisible(policy.shows_yield_counters)
        self.dut_label.setVisible(policy.requires_dut_serial)

        # Repeat settings change the verdict criteria, so they are locked in
        # operator mode.
        self.seq_repeat_box.setEnabled(policy.can_edit_criteria)
        self.lbl_seq_repeat.setEnabled(policy.can_edit_criteria)
        self.act_stop_on_fail.setEnabled(policy.can_edit_criteria)
        self.act_stop_batch_on_fail.setEnabled(policy.can_edit_criteria)

        if mode is AppMode.OPERATOR:
            reader_card_id = None
            for cid, cwidget in self.drop_zone.cards.items():
                if getattr(cwidget.module, "module_id", "") == "dut_serialnumber_reader":
                    reader_card_id = cid
                    break

            if reader_card_id is None:
                self.add_card_to_dashboard("dut_serialnumber_reader", insert_idx=0, check_deps=False)
            else:
                self.ensure_config_cards_at_top()

        for card in self.drop_zone.cards.values():
            card.set_editable(can_edit, policy.can_add_remove_cards, policy.can_reorder_cards)

        self.update_yield_display()

    def update_yield_display(self) -> None:
        stats = self.yield_stats
        self.lbl_yield_total.setText(str(stats.total))
        self.lbl_yield_pass.setText(str(stats.passed))
        self.lbl_yield_fail.setText(str(stats.failed))
        self.lbl_yield_pct.setText(f"{stats.yield_pct:.1f}%")

        # State the scope: numbers cannot be trusted when what they count is
        # invisible.
        if self.yield_scope_since == self._today_start():
            self.yield_scope_label = language.tr(
                "yield_scope_today", date=datetime.date.today().isoformat()
            )
        else:
            self.yield_scope_label = language.tr("yield_scope_session")

        tip = (language.tr("yield_top_failure", module=stats.top_failure)
               if stats.top_failure else language.tr("yield_no_failure"))
        cumulative = self.trace_log.stats()
        self.yield_container.setToolTip(language.tr(
            "tooltip_yield_detail",
            scope=self.yield_scope_label, failure=tip,
            total=cumulative.total, yield_pct=f"{cumulative.yield_pct:.1f}",
            path=self.trace_log.jsonl_path,
        ))
        if self.current_dut_serial:
            self.dut_label.setText(language.tr("dut_set", serial=self.current_dut_serial))
        else:
            self.dut_label.setText(language.tr("dut_unset"))

    # ==========================================================================
    # Sequence execution - build a recipe, hand it to the core, record the trace.
    #
    # The previous structure ran one card, then busy-waited on processEvents plus
    # sleep(0.02) before moving to the next. That left the UI thread holding the
    # sequence, so the screen was sluggish while running and headless
    # verification was impossible. The sequence now runs on a worker thread and
    # only progress comes back as signals.
    # ==========================================================================
    def run_all_cards(self):
        if self.is_running_all:
            return

        recipe = self.build_recipe_from_cards()
        if not recipe.steps:
            QMessageBox.information(self, language.tr("msg_run_seq_title"), language.tr("msg_no_cards"))
            return

        policy = self.mode_controller.policy
        use_mock = self.mock_checkbox.isChecked()

        # Testing in mock mode on a production line ships fabricated results.
        if policy.forces_real_hardware and use_mock:
            QMessageBox.warning(
                self, language.tr("msg_operator_mode_title"), language.tr("msg_mock_forbidden")
            )
            return

        # Traceability: which board was tested has to be recorded.
        dut_serial = self.current_dut_serial

        self.is_running_all = True
        self.stop_requested = False
        self.set_run_all_button_mode(is_stop_mode=True)

        # Reset card state; step_id maps to card_id
        self._sequence_logs = {}
        for card_widget in self.drop_zone.cards.values():
            card_widget.status = "READY"
            card_widget.summary_text = ""
            card_widget.live_logs = []
            card_widget.set_iteration(0, 0)

        steps_per_run = count_step_executions(recipe)
        repeat = self.spin_seq_repeat.value()
        self.progress_bar.setRange(0, max(1, steps_per_run))
        self.progress_bar.setValue(0)
        self.set_progress_text(language.tr("status_progress", done=0, total=steps_per_run, pct="0"))
        if repeat == 0:
            self.lbl_run_progress.setText(language.tr("status_repeat_infinite"))
        elif repeat > 1:
            self.lbl_run_progress.setText(language.tr("status_run_of", index=1, total=repeat))
        else:
            self.lbl_run_progress.setText("")

        context = SequenceContext(
            dut_serial=dut_serial,
            mode=self.mode_controller.mode.value,
            station=self.station_name,
            operator=self.operator_name,
            use_mock=use_mock,
            timestamp=self._now_iso(),
        )

        self.sequence_thread = SequenceRunnerThread(
            recipe, LOADED_MODULES, context,
            trace_log=self.trace_log,
            stop_on_fail=self.stop_on_fail,
            sequence_repeat=self.spin_seq_repeat.value(),
            stop_batch_on_fail=self.stop_batch_on_fail,
            parent=self,
        )
        self.sequence_thread.step_started.connect(self.on_sequence_step_started)
        self.sequence_thread.step_log.connect(self.on_sequence_step_log)
        self.sequence_thread.step_finished.connect(self.on_sequence_step_finished)
        self.sequence_thread.progress_changed.connect(self.on_sequence_progress)
        self.sequence_thread.run_started.connect(self.on_run_started)
        self.sequence_thread.run_finished.connect(self.on_run_finished)
        self.sequence_thread.batch_finished.connect(self.on_batch_finished)
        self.sequence_thread.batch_failed.connect(self.on_sequence_failed)
        self.sequence_thread.start()

    # ---------------------------------------------------------- progress updates
    @Slot(str, int, int, int)
    def on_sequence_step_started(self, step_id: str, index: int, iteration: int, iterations: int):
        card = self.drop_zone.cards.get(step_id)
        if not card:
            return
        card.status = "RUNNING"
        card.start_run_time = time.time()
        card.execution_time = "0.0s"
        card.live_logs = []
        card.set_iteration(iteration, iterations)
        card.elapsed_timer.start()
        card.update_style()

        if hasattr(self, "live_console"):
            c_name = card.card.get_localized("name", language.current_lang) if hasattr(card, "card") else step_id
            self.live_console.append_log(f"--- [{step_id}] Started: {c_name} (Pass {iteration}/{iterations}) ---", step_id=step_id)

    @Slot(str, str)
    def on_sequence_step_log(self, step_id: str, message: str):
        card = self.drop_zone.cards.get(step_id)
        if card is not None:
            card.live_logs.append(message)
        self._sequence_logs.setdefault(step_id, []).append(message)
        if hasattr(self, "live_console"):
            self.live_console.append_log(message, step_id=step_id)

    @Slot(str, int, dict)
    def on_sequence_step_finished(self, step_id: str, iteration: int, result: dict):
        card = self.drop_zone.cards.get(step_id)
        if not card:
            return
        if card.elapsed_timer.isActive():
            card.elapsed_timer.stop()
        card.handle_thread_finished(result)

        # Propagate acquired serial number to GUI state & label
        new_serial = result.get("dut_serial") or (result.get("details", {}).get("metrics", {}) or {}).get("dut_serial")
        if new_serial:
            self.current_dut_serial = str(new_serial)
            self.dut_label.setText(language.tr("dut_set", serial=self.current_dut_serial))

        if hasattr(self, "live_console"):
            res_str = result.get("result", "DONE")
            summary = result.get("summary_text", "")
            self.live_console.append_log(f"[{step_id}] Result: {res_str} - {summary}", step_id=step_id)

    @Slot(object, int, int)
    def on_run_finished(self, seq_result, index: int, total: int):
        """Update the yield figures and the report after each pass."""
        record = seq_result.record
        if record.dut_serial:
            self.current_dut_serial = str(record.dut_serial)
            self.dut_label.setText(language.tr("dut_set", serial=self.current_dut_serial))
        self.yield_stats.register(record)
        self.last_run_record = record
        self.last_report_data = self._report_data_from_record(record)
        self.update_yield_display()

        if hasattr(self, "live_console"):
            self.live_console.append_log(f"=== Pass {index}/{total} Complete: {record.steps_passed}/{record.steps_total} Steps Passed ===")

    @Slot(list)
    def on_batch_finished(self, results: list):
        self.is_running_all = False
        self.stop_requested = False
        self.set_run_all_button_mode(is_stop_mode=False)
        self.update_recipe_display()

        for card in self.drop_zone.cards.values():
            card.set_iteration(0, 0)

        if not results:
            self.set_progress_text(language.tr("status_no_runs"))
            return

        last = results[-1]
        passed = sum(1 for r in results if r.passed)
        cancelled = any(r.cancelled for r in results)

        if len(results) > 1:
            self.set_progress_text(
                language.tr("status_batch_done", passed=passed, total=len(results))
            )
        else:
            self.set_progress_text(language.tr(
                "status_single_done", result=last.result,
                passed=last.record.steps_passed, total=last.record.steps_total,
            ))
        self.lbl_run_progress.setText(language.tr("status_cancelled_short") if cancelled else "")

        if hasattr(self, "live_console"):
            status_summary = "CANCELLED" if cancelled else ("ALL PASSED" if passed == len(results) else f"{passed}/{len(results)} PASSED")
            self.live_console.append_log(f"■■ Batch Finished: {status_summary}")

        if cancelled:
            self.statusBar().showMessage(language.tr("status_seq_cancelled"), 8000)
        else:
            self.statusBar().showMessage(language.tr(
                "status_finished", result=last.result,
                dut=last.record.dut_serial or "-", runs=len(results),
                path=self.trace_log.jsonl_path,
            ), 12000)

        if self.auto_report_checkbox.isChecked() and not cancelled:
            dialog = TestReportDialog(self.last_report_data, self)
            dialog.exec()

    @Slot(str)
    def on_sequence_failed(self, message: str):
        self.is_running_all = False
        self.stop_requested = False
        self.set_run_all_button_mode(is_stop_mode=False)
        QMessageBox.critical(self, language.tr("msg_seq_error_title"),
                             language.tr("msg_seq_error_body", error=message))

    def _report_data_from_record(self, record) -> dict:
        """Convert a trace record into the existing report format."""
        cards_summary = []
        all_logs = [language.tr(
            "report_suite_start", time=record.started_at, dut=record.dut_serial or "-",
            recipe=f"{record.recipe_name} v{record.recipe_version}",
            fp=record.recipe_fingerprint[:12],
        )]

        for step in record.steps:
            module = get_module(LOADED_MODULES, step.module_id)
            mod_name = module.get_localized("name", language.current_lang) if module else step.module_id
            entry = {
                "card_id": step.step_id,
                "name": mod_name,
                "status": step.result,
                "execution_time_sec": step.execution_time_sec,
                "summary_text": step.summary_text,
                # Index into all_logs where this step's section header sits, so
                # the report dialog can jump straight to it. Searching the text
                # by module name would land on the wrong section when the same
                # module appears twice in a sequence.
                "log_index": None,
            }
            cards_summary.append(entry)
            if step.logs:
                entry["log_index"] = len(all_logs)
                all_logs.append("\n" + language.tr("report_module_logs", name=mod_name))
                all_logs.extend(step.logs)

        if record.cancelled:
            all_logs.append("\n" + language.tr("report_suite_cancel", time=record.finished_at))
        else:
            all_logs.append("\n" + language.tr(
                "report_suite_end", time=record.finished_at, result=record.result,
                passed=record.steps_passed, total=record.steps_total,
            ))

        return {"cards_summary": cards_summary, "all_logs": all_logs, "run_record": record}

    def compile_current_report_data(self) -> dict:
        """Dynamically compiles real-time execution state and logs from all dashboard cards"""
        cards_summary = []
        all_logs = [f"[REPORT GENERATED] {time.strftime('%Y-%m-%d %H:%M:%S')} Live Dashboard State"]
        
        # Config cards are included. Skipping them was how the same button
        # produced two different reports: the dialog shown after a run comes
        # from the recorded run, which has every step, while this one dropped
        # the session cards - so a config card failure vanished
        # from the very report an operator would open to look for it.
        for card_widget in self.drop_zone.cards.values():
            exec_time = 0.0
            try:
                exec_time = float(card_widget.execution_time.replace("s", "").strip())
            except ValueError:
                exec_time = 0.0

            mod_name = card_widget.module.get_localized("name", language.current_lang)

            entry = {
                "card_id": card_widget.card_id,
                "name": mod_name,
                "status": card_widget.status,
                "execution_time_sec": exec_time,
                "summary_text": card_widget.summary_text,
                "log_index": None,   # see _report_data_from_record
            }
            cards_summary.append(entry)

            res = card_widget.last_result
            if res and "details" in res and "logs" in res["details"]:
                entry["log_index"] = len(all_logs)
                all_logs.append(f"\n--- [{mod_name}] Logs ---")
                all_logs.extend(res["details"]["logs"])

        return {
            "cards_summary": cards_summary,
            "all_logs": all_logs
        }

    def open_last_report(self):
        """
        Show the report for the last recorded run, falling back to the live
        dashboard state when nothing has run yet.

        It used to always recompile from the dashboard, which meant this button
        and the dialog shown automatically after a run disagreed about the same
        test session - different step counts and a different verdict. One label
        has to mean one thing, so the recorded run wins whenever there is one.
        """
        data = self.last_report_data
        if not (data and data.get("run_record")):
            data = self.compile_current_report_data()
            self.last_report_data = data
        dialog = TestReportDialog(data, self)
        dialog.exec()

    def on_card_clicked(self, card_id: str):
        if card_id not in self.drop_zone.cards:
            return
        card_widget = self.drop_zone.cards[card_id]

        # Decided by module_type rather than module_id: a config card opens its
        # settings screen, a test card its results. Adding a new config module
        # leaves this code untouched.
        if card_widget.module.module_type == "config":
            dialog = make_criteria_dialog(card_widget.module, card_widget.criteria, parent=self)
            if dialog.exec() == QDialog.Accepted:
                card_widget.criteria = dialog.get_criteria()
                card_widget.update_style()
                self.save_dashboard_state()
        else:
            dialog = DetailDialog(card_widget, self)
            dialog.exec()


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()
