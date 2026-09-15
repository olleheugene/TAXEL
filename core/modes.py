"""
Application modes and their permission policy.

A production-line operator and an engineer running development tests cannot
share the same screen. Exposing an nrfutil path field and an editable current
limit to an operator is an accident waiting to happen.

The policy is defined here and nowhere else. Frontends query it to hide or
disable widgets; the core refuses edits to a locked recipe.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from dataclasses import dataclass
from enum import Enum
from typing import Optional

from core.language import language


class AppMode(str, Enum):
    OPERATOR = "operator"   # production: select a recipe and run it, nothing else
    ENGINEER = "engineer"   # development: edit freely

    @property
    def display_name(self) -> str:
        return language.tr("mode_operator" if self is AppMode.OPERATOR else "mode_engineer")

    @property
    def badge(self) -> str:
        return "🔒" if self is AppMode.OPERATOR else "🔧"


@dataclass(frozen=True)
class ModePolicy:
    """What a mode permits. Frontends build their UI from these flags alone."""

    mode: AppMode

    @property
    def can_edit_criteria(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_add_remove_cards(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_reorder_cards(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_edit_recipe(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_import_recipe(self) -> bool:
        # Operators must be able to receive a distributed recipe, otherwise the
        # line cannot be run at all.
        return True

    @property
    def can_export_recipe(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_toggle_mock(self) -> bool:
        # Testing in mock mode on a production line ships fabricated results.
        return self.mode is AppMode.ENGINEER

    @property
    def can_reload_modules(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def can_run(self) -> bool:
        return True

    @property
    def requires_dut_serial(self) -> bool:
        # Traceability: in production, which board was tested must be recorded.
        return self.mode is AppMode.OPERATOR

    @property
    def shows_palette(self) -> bool:
        return self.mode is AppMode.ENGINEER

    @property
    def shows_yield_counters(self) -> bool:
        return self.mode is AppMode.OPERATOR

    @property
    def forces_real_hardware(self) -> bool:
        return self.mode is AppMode.OPERATOR


class ModeController:
    """
    Holds the current mode and gates entry into engineer mode.

    The passcode is optional. Without one anybody can switch, so a production
    deployment must set it (check with is_protected).
    """

    def __init__(self, mode: AppMode = AppMode.ENGINEER, passcode: Optional[str] = None):
        self._mode = mode
        self._passcode = passcode or None

    @property
    def mode(self) -> AppMode:
        return self._mode

    @property
    def policy(self) -> ModePolicy:
        return ModePolicy(self._mode)

    @property
    def is_protected(self) -> bool:
        return bool(self._passcode)

    def to_operator(self) -> None:
        """Dropping down to operator mode is always allowed."""
        self._mode = AppMode.OPERATOR

    def to_engineer(self, passcode: Optional[str] = None) -> bool:
        """
        Move up to engineer mode. If a passcode is configured it must match.
        :return: whether the switch succeeded
        """
        if self._passcode and passcode != self._passcode:
            return False
        self._mode = AppMode.ENGINEER
        return True

    def set_passcode(self, passcode: Optional[str]) -> None:
        self._passcode = passcode or None
