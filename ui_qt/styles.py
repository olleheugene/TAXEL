"""
Qt stylesheets shared between the main window and the dialogs.

Kept here rather than duplicated so that a widget looks the same wherever it
appears. Qt-only: importing this pulls in no framework code.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))


# A spin box on a dark surface.
#
# The field used to be #0f172a with a barely-there border, which made the
# up/down button area indistinguishable from the field: Qt does draw the arrows,
# but they occupy about 3% of that area, so with no boundary around them the
# right-hand end of the control read as empty space.
#
# The buttons are deliberately NOT styled. Styling QSpinBox::up-button stops Qt
# from drawing its native arrow, and QSS has no way to draw a triangle - the CSS
# border trick renders as a filled rectangle here. What actually fixes it is
# lightening the field: the native button frames and their white arrows are then
# legible against it. Measured on all four candidates - the background does the
# work, the border colour barely matters - so the border stays identical to the
# other inputs on the same form (schema_form.INPUT_STYLE) and nothing looks out
# of place next to a combo box or a text field.
SPINBOX_QSS = """
QSpinBox, QDoubleSpinBox {
    background-color: #1e293b;
    border: 1px solid rgba(255, 255, 255, 0.2);
    border-radius: 6px;
    padding: 4px;
    color: #f8fafc;
}
QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #3b82f6;
}
QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #172033;
    border-color: #334155;
    color: #64748b;
}
"""

# The explicit up/down buttons next to a spin box (ui_qt.spinbox.with_steppers).
# Same visual language as the up/down buttons on a dashboard card, just smaller.
STEPPER_BTN_QSS = """
QPushButton {
    background-color: rgba(255, 255, 255, 0.12);
    border: 1px solid rgba(255, 255, 255, 0.22);
    border-radius: 4px;
    color: #e2e8f0;
    font-size: 8px;
    padding: 0px;
}
QPushButton:hover {
    background-color: #3b82f6;
    border-color: #60a5fa;
    color: #ffffff;
}
QPushButton:pressed {
    background-color: #1d4ed8;
    border-color: #93c5fd;
}
QPushButton:disabled {
    background-color: rgba(255, 255, 255, 0.04);
    border-color: #334155;
    color: #475569;
}
"""
