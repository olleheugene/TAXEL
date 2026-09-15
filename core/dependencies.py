"""
Card prerequisite and shared instrument dependency checks.

Some cards cannot run on their own because they borrow a shared instrument
session (e.g. Serial, PPK2, or other hardware instruments).

Prerequisites are established two ways:
  1. Derived automatically - via capabilities (e.g. capabilities["needs_serial"] == True)
     matched against config cards that own those instruments.
  2. Declared explicitly - requires_modules / requires_cards = ["card_id", ...]

This module provides an extensible Instrument Provider registry so new instruments
(J-Link, Power Supplies, Oscilloscopes, Relays, etc.) can be added as plugin cards
without modifying core code.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Tuple

from core.language import language
from core.registry import get_card, resolve_card_id, get_module, resolve_module_id

SERIAL_OWNER_REQUIREMENT = "serial_session_owner"
PPK_OWNER_REQUIREMENT = "ppk_session_owner"


@dataclass
class InstrumentProviderSpec:
    """Specification for a shared instrument provider."""
    capability: str              # e.g. "needs_serial", "needs_ppk"
    kind: str                    # requirement kind identifier
    override_key: str            # criteria key that overrides shared session
    display_title_key: str = ""  # localization key for dialog title
    display_body_key: str = ""   # localization key for dialog body
    default_priority: int = 50   # default sorting priority for dashboard


INSTRUMENT_REGISTRY: Dict[str, InstrumentProviderSpec] = {}


def register_instrument_provider(spec: InstrumentProviderSpec):
    """Register a shared instrument provider specification."""
    INSTRUMENT_REGISTRY[spec.capability] = spec


# Built-in instrument providers
# Note: DUT Serial is now globally configured via the Global Device Toolbar and CLI.
# Instruments that still use dedicated config cards (e.g. PPK2) remain registered here.
register_instrument_provider(
    InstrumentProviderSpec(
        capability="needs_ppk",
        kind=PPK_OWNER_REQUIREMENT,
        override_key="ppk_port",
        display_title_key="dep_ppk_title",
        display_body_key="dep_ppk_missing_body",
        default_priority=10,
    )
)

SESSION_OWNERS = tuple(
    {
        "capability": spec.capability,
        "kind": spec.kind,
        "override_key": spec.override_key,
    }
    for spec in INSTRUMENT_REGISTRY.values()
)


@dataclass
class Requirement:
    """One unmet prerequisite."""

    kind: str                    # requirement kind or "card"
    card_id: str = ""            # the card that resolves it; empty = not auto-fixable
    requester_id: str = ""
    installed: bool = True       # whether that card exists on this station
    card_name: str = ""          # localized display name

    # Backward compatibility aliases
    @property
    def module_id(self) -> str:
        return self.card_id

    @module_id.setter
    def module_id(self, val: str):
        self.card_id = val

    @property
    def module_name(self) -> str:
        return self.card_name

    @module_name.setter
    def module_name(self, val: str):
        self.card_name = val

    @property
    def fixable(self) -> bool:
        """Whether adding one card resolves it."""
        return bool(self.card_id) and self.installed

    def describe(self) -> str:
        """Multi-line explanation shown to the user."""
        display = self.card_name or self.card_id
        if self.kind == SERIAL_OWNER_REQUIREMENT:
            if not self.card_id:
                return language.tr("dep_reason_serial_owner_none")
            return language.tr("dep_reason_serial_owner", module=display)
        if self.kind == PPK_OWNER_REQUIREMENT:
            if not self.card_id:
                return language.tr("dep_reason_ppk_owner_none")
            return language.tr("dep_reason_ppk_owner", module=display)
        if not self.installed:
            return language.tr("dep_reason_module_missing",
                               module=display, requester=self.requester_id)
        return language.tr("dep_reason_module",
                           module=display, requester=self.requester_id)

    def short(self) -> str:
        """One-line summary for a card badge or tooltip."""
        display = self.card_name or self.card_id
        if self.kind == SERIAL_OWNER_REQUIREMENT:
            return language.tr("dep_short_serial_owner")
        if self.kind == PPK_OWNER_REQUIREMENT:
            return language.tr("dep_short_ppk_owner")
        return language.tr("dep_short_module", module=display)


@dataclass
class DependencyReport:
    unmet: List[Requirement] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.unmet

    @property
    def fixable(self) -> bool:
        """Whether every unmet item can be resolved by adding a card."""
        return bool(self.unmet) and all(r.fixable for r in self.unmet)

    def cards_to_add(self) -> List[str]:
        seen: List[str] = []
        for r in self.unmet:
            if r.fixable and r.card_id not in seen:
                seen.append(r.card_id)
        return seen

    def modules_to_add(self) -> List[str]:
        return self.cards_to_add()

    def describe(self) -> str:
        return "\n\n".join(r.describe() for r in self.unmet)

    def short(self) -> str:
        return ", ".join(r.short() for r in self.unmet)

    def get_prompt(self, requester_name: str) -> Tuple[str, str]:
        """
        Dynamically determine the appropriate prompt title and text for adding missing cards.
        Decoupled from specific instruments.
        """
        unmet_kinds = {r.kind for r in self.unmet}
        to_add = self.cards_to_add()

        # Check if it matches a registered instrument provider
        for spec in INSTRUMENT_REGISTRY.values():
            if unmet_kinds == {spec.kind} or (len(to_add) == 1 and to_add[0].lower().startswith(spec.capability.replace("needs_", "config_"))):
                if spec.display_title_key and spec.display_body_key:
                    title = language.tr(spec.display_title_key)
                    body = language.tr(spec.display_body_key, module=requester_name)
                    return title, body

        # Single card requirement
        if len(self.unmet) == 1 and self.unmet[0].card_name:
            provider_name = self.unmet[0].card_name
            # If standard language keys exist
            if "serial" in self.unmet[0].card_id.lower():
                return language.tr("dep_serial_title"), language.tr("dep_serial_missing_body", module=requester_name)
            if "ppk" in self.unmet[0].card_id.lower():
                return language.tr("dep_ppk_title"), language.tr("dep_ppk_missing_body", module=requester_name)
            # Extensible dynamic fallback
            title = f"{provider_name} 카드 필요"
            body = f"'{requester_name}' 카드를 실행하려면 {provider_name} 카드가 필요합니다.\n대시보드에 {provider_name} 카드를 함께 추가하시겠습니까?"
            return title, body

        # General multi-requirement fallback
        return language.tr("dep_error_title"), language.tr("dep_missing_body", module=requester_name, reasons=self.describe())


def find_serial_session_owner(cards: Dict[str, Any]) -> Optional[str]:
    """Find the id of the config card that opens the serial session."""
    return find_session_owner(cards, "needs_serial")


def find_session_owner(cards: Dict[str, Any], capability: str) -> Optional[str]:
    """
    Find the config card that owns the session for `capability`.
    Located by card_type == 'config' and capability flag.
    """
    for card_id, card in cards.items():
        card_type = getattr(card, "card_type", getattr(card, "module_type", "test"))
        if card_type == "config":
            # Check capability flag attribute or capabilities dictionary
            if getattr(card, capability, False):
                return card_id
            caps = getattr(card, "capabilities", {}) or {}
            if caps.get(capability, False):
                return card_id
            # Check provides_instrument
            provides = getattr(card, "provides_instrument", "")
            if provides and capability == f"needs_{provides}":
                return card_id
    return None


def declared_requirements(card) -> List[str]:
    """The card ids a card declares via requires_cards or requires_modules."""
    raw = getattr(card, "requires_cards", None) or getattr(card, "requires_modules", None) or []
    if isinstance(raw, str):
        raw = [raw]
    return [str(x) for x in raw if str(x)]


def has_port_override(criteria: Optional[Dict[str, Any]]) -> bool:
    if not criteria:
        return False
    return bool(str(criteria.get("port", "") or "").strip())


def has_own_device(criteria: Optional[Dict[str, Any]], override_key: str) -> bool:
    if not criteria:
        return False
    return bool(str(criteria.get(override_key, "") or "").strip())


def check_dependencies(
    card,
    present_card_ids: Iterable[str],
    cards: Dict[str, Any],
    criteria: Optional[Dict[str, Any]] = None,
) -> DependencyReport:
    """
    Check whether a card may be added to the dashboard.
    """
    present = {resolve_card_id(cards, cid) or cid for cid in present_card_ids}
    report = DependencyReport()
    card_id = getattr(card, "card_id", getattr(card, "module_id", "card"))
    card_type = getattr(card, "card_type", getattr(card, "module_type", "test"))

    # 1) Session owners - derived from registered instrument providers
    for spec in INSTRUMENT_REGISTRY.values():
        capability = spec.capability
        card_needs_it = getattr(card, capability, False) or (getattr(card, "capabilities", {}) or {}).get(capability, False)
        if not card_needs_it:
            continue
        if card_type == "config":
            continue
        if has_own_device(criteria, spec.override_key):
            continue

        owner_present = any(
            (get_card(cards, cid) is not None
             and getattr(get_card(cards, cid), "card_type", getattr(get_card(cards, cid), "module_type", "test")) == "config"
             and (getattr(get_card(cards, cid), capability, False) or (getattr(get_card(cards, cid), "capabilities", {}) or {}).get(capability, False)))
            for cid in present
        )
        if owner_present:
            continue

        owner_id = find_session_owner(cards, capability)
        owner_card = get_card(cards, owner_id) if owner_id else None
        owner_name = owner_card.get_localized("name", language.current_lang) if owner_card else (owner_id or "")
        report.unmet.append(
            Requirement(
                kind=spec.kind,
                card_id=owner_id or "",
                requester_id=card_id,
                installed=bool(owner_id),
                card_name=owner_name,
            )
        )

    # 2) Explicit declarations
    for required_id in declared_requirements(card):
        canonical = resolve_card_id(cards, required_id) or required_id
        if canonical in present:
            continue
        req_card = get_card(cards, canonical)
        req_name = req_card.get_localized("name", language.current_lang) if req_card else canonical
        report.unmet.append(
            Requirement(
                kind="card",
                card_id=canonical,
                requester_id=card_id,
                installed=req_card is not None,
                card_name=req_name,
            )
        )

    return report


def find_dependents(
    card_id: str,
    present_card_ids: Iterable[str],
    cards: Dict[str, Any],
) -> List[str]:
    """
    Find dashboard cards that would become unrunnable if this one is removed.
    """
    present = [cid for cid in present_card_ids if cid != card_id]
    target = get_card(cards, card_id)
    if target is None:
        return []

    target_type = getattr(target, "card_type", getattr(target, "module_type", "test"))
    dependents: List[str] = []

    owned = [
        spec.capability for spec in INSTRUMENT_REGISTRY.values()
        if target_type == "config" and (getattr(target, spec.capability, False) or (getattr(target, "capabilities", {}) or {}).get(spec.capability, False))
    ]

    for cid in present:
        other = get_card(cards, cid)
        if other is None:
            continue

        other_type = getattr(other, "card_type", getattr(other, "module_type", "test"))
        borrows_from_target = False
        for capability in owned:
            other_needs_it = getattr(other, capability, False) or (getattr(other, "capabilities", {}) or {}).get(capability, False)
            if not other_needs_it or other_type == "config":
                continue
            # Not a problem if another owner of that same session remains.
            if any(
                (get_card(cards, p) is not None
                 and getattr(get_card(cards, p), "card_type", getattr(get_card(cards, p), "module_type", "test")) == "config"
                 and (getattr(get_card(cards, p), capability, False) or (getattr(get_card(cards, p), "capabilities", {}) or {}).get(capability, False)))
                for p in present
            ):
                continue
            borrows_from_target = True
            break

        if borrows_from_target:
            dependents.append(cid)
            continue

        required = {resolve_card_id(cards, r) or r for r in declared_requirements(other)}
        if card_id in required:
            dependents.append(cid)

    seen: List[str] = []
    for cid in dependents:
        if cid not in seen:
            seen.append(cid)
    return seen
