"""
Legacy alias for modules.base_card.

Provided for backward compatibility. New cards should import BaseCard from
modules.base_card (or core.schema).
"""

from cards.base_card import (
    BaseCard,
    BaseTestModule,
    SerialLogParser,
    CANCEL_CRITERIA_KEY,
    SERIAL_CRITERIA_KEY,
    SerialSession,
    SerialSessionError,
    serial_registry,
    session_from_criteria,
    PPKSession,
    PPKSessionError,
    ppk_session_from_criteria,
)

__all__ = [
    "BaseCard",
    "BaseTestModule",
    "SerialLogParser",
    "CANCEL_CRITERIA_KEY",
    "SERIAL_CRITERIA_KEY",
    "SerialSession",
    "SerialSessionError",
    "PPKSession",
    "PPKSessionError",
]
