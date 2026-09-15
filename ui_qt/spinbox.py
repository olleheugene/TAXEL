"""
Spin box with explicit step buttons.

Why not the native ones: under this application's dark stylesheet the macOS
style draws the up/down button frames but **no arrows at all** - measured, the
button area contains zero pixels brighter than the field. Fusion and Windows do
draw them, which is why an offscreen check passes while the real window shows
empty boxes.

Nor can the arrows be drawn in the stylesheet: styling `QSpinBox::up-button`
stops Qt from painting its native indicator, and QSS has no triangle primitive
(the CSS border trick renders as a filled rectangle).

So the buttons are ordinary QPushButtons carrying the arrow glyphs, exactly like
the up/down buttons already on the dashboard cards. Nothing about their
appearance is left to the platform style.

Native behaviour is preserved: press-and-hold repeats, the keyboard Up/Down keys
and the mouse wheel still work, since only the button symbols are removed from
the spin box, not its stepping logic.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox,
    QHBoxLayout,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from ui_qt.styles import SPINBOX_QSS, STEPPER_BTN_QSS

# Button geometry. Two stacked buttons plus the gap between them are sized to
# match the spin box height used across the app (32 px).
STEP_BTN_WIDTH = 22
STEP_BTN_HEIGHT = 14
STEP_GAP = 2

# Press-and-hold, matching what the native buttons did.
AUTO_REPEAT_DELAY_MS = 400
AUTO_REPEAT_INTERVAL_MS = 80


def _step_button(glyph: str, on_click) -> QPushButton:
    btn = QPushButton(glyph)
    btn.setFixedSize(STEP_BTN_WIDTH, STEP_BTN_HEIGHT)
    btn.setStyleSheet(STEPPER_BTN_QSS)
    btn.setFocusPolicy(Qt.NoFocus)          # tabbing should reach the field, not these
    btn.setAutoRepeat(True)
    btn.setAutoRepeatDelay(AUTO_REPEAT_DELAY_MS)
    btn.setAutoRepeatInterval(AUTO_REPEAT_INTERVAL_MS)
    btn.clicked.connect(on_click)
    return btn


def with_steppers(box: QAbstractSpinBox, field_width: int = 0) -> QWidget:
    """
    Style `box`, replace its native buttons with explicit ones, and return the
    container widget to place in a layout.

    :param box:         a QSpinBox or QDoubleSpinBox
    :param field_width: fixed width for the number field; 0 leaves it flexible
    :return:            the container. The spin box itself stays the value
                        source - read `box.value()` as before.
    """
    box.setStyleSheet(SPINBOX_QSS)
    box.setButtonSymbols(QAbstractSpinBox.NoButtons)
    box.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)
    if field_width:
        box.setFixedWidth(field_width)

    container = QWidget()
    row = QHBoxLayout(container)
    row.setContentsMargins(0, 0, 0, 0)
    row.setSpacing(4)

    column = QVBoxLayout()
    column.setContentsMargins(0, 0, 0, 0)
    column.setSpacing(STEP_GAP)
    column.addWidget(_step_button("▲", box.stepUp))    # BLACK UP-POINTING TRIANGLE
    column.addWidget(_step_button("▼", box.stepDown))  # BLACK DOWN-POINTING TRIANGLE

    row.addWidget(box)
    row.addLayout(column)
    return container
