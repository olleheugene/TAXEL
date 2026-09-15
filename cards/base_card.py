# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import os
import time
import re
from abc import ABC, abstractmethod
from typing import Callable, Dict, Any, List, Optional, Tuple

from cards.serial_session import (
    CRITERIA_KEY as SERIAL_CRITERIA_KEY,
    SerialSession,
    SerialSessionError,
    serial_registry,
    session_from_criteria,
)
# Key the framework injects the cancellation poll under. A card reads it
# through cancelled() / cancel_check() rather than by name.
CANCEL_CRITERIA_KEY = "_is_cancelled"

from cards.ppk_session import (
    PPKSession,
    PPKSessionError,
    session_from_criteria as ppk_session_from_criteria,
)


class SerialLogParser:
    """
    Shared utility for reading serial data and extracting numbers with regular
    expressions. Usable from any card that needs to read serial logs and pull
    numeric values out of them.
    """
    @staticmethod
    def extract_numbers(text: str, patterns: Dict[str, str]) -> Dict[str, Optional[float]]:
        """
        Extract numbers as floats from a block of text using a dict of regex
        patterns.
        :param text: the full received serial text
        :param patterns: { "name": r"regex_pattern" }
        :return: { "name": float or None }
        """
        results = {}
        for name, pattern in patterns.items():
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    results[name] = float(match.group(1))
                except (ValueError, IndexError):
                    results[name] = None
            else:
                results[name] = None
        return results

    @staticmethod
    def read_and_parse(
        port: str,
        baudrate: int = 115200,
        timeout_sec: float = 3.0,
        patterns: Dict[str, str] = None,
        use_mock: bool = False,
        mock_lines: List[str] = None,
        session: Optional[SerialSession] = None
    ) -> Tuple[List[str], Dict[str, Optional[float]]]:
        """
        Collect log lines from the shared serial session and parse numbers.

        Never opens a port directly: an injected session is used when given,
        otherwise the session for that port is acquired from the shared store.
        Either way there is exactly one handle.

        :return: (logs_list, parsed_dict)
        """
        logs = []
        parsed_results = {}
        accumulated_text = ""

        is_mock = use_mock or not port or port == "COM1"

        if session is None:
            try:
                session = serial_registry.acquire(
                    port=port or "mock",
                    baudrate=baudrate,
                    mock=is_mock,
                    log_callback=None,
                )
            except SerialSessionError as e:
                logs.append(f"[SERIAL ERR] {e}")
                if patterns:
                    parsed_results = SerialLogParser.extract_numbers("", patterns)
                return logs, parsed_results

        if session.mock:
            session.feed_mock_lines(mock_lines or [])
        else:
            logs.append(f"[SERIAL INIT] Using shared session: {port} @ {baudrate} bps (Timeout: {timeout_sec}s)")

        try:
            for line in session.read_lines(timeout_sec):
                logs.append(f"[SERIAL RX] {line}")
                accumulated_text += line + "\n"
        except SerialSessionError as e:
            logs.append(f"[SERIAL ERR] Serial read failed ({port}): {e}")

        if patterns:
            parsed_results = SerialLogParser.extract_numbers(accumulated_text, patterns)

        return logs, parsed_results


class BaseCard(ABC):
    """
    Base contract for every test or configuration card in nRF Test Suite.

    Cards are self-contained plugin units that declare:
      - card_id: permanent unique identifier
      - default_criteria: configurable criteria schema
      - capabilities: required shared instruments (e.g. needs_serial, needs_ppk)
      - run(criteria, use_mock): execution logic
    """

    info: Dict[str, Any] = {}
    default_criteria: Dict[str, Dict[str, Any]] = {}
    help_image_path: str = ""
    translations: Dict[str, Any] = {}

    # Shared resources the card requires. The framework reads this, prepares
    # the resource and injects it. Cards never open resources themselves.
    capabilities: Dict[str, bool] = {}

    def __init__(self):
        self.translations = {}
        self.load_translations()

    def load_translations(self):
        """
        Automatically loads translations from the card's own directory
        (language/<lang>.json or translations.json).
        """
        try:
            import inspect
            import json
            import sys

            card_dir = None
            try:
                card_file = inspect.getfile(self.__class__)
                if card_file and os.path.exists(card_file):
                    card_dir = os.path.dirname(os.path.abspath(card_file))
            except Exception:
                pass

            if not card_dir:
                try:
                    mod_obj = sys.modules.get(self.__module__)
                    if mod_obj and hasattr(mod_obj, "__file__") and mod_obj.__file__:
                        card_dir = os.path.dirname(os.path.abspath(mod_obj.__file__))
                except Exception:
                    pass

            if not card_dir:
                base_dir = os.path.dirname(os.path.abspath(__file__))
                candidate = os.path.join(base_dir, self.card_id)
                if os.path.exists(candidate):
                    card_dir = candidate

            if card_dir:
                from core.language import load_language_dir

                per_language = load_language_dir(os.path.join(card_dir, "language"))
                for lang, mapping in per_language.items():
                    self.translations.setdefault(lang, {}).update(mapping)

                legacy = os.path.join(card_dir, "translations.json")
                if os.path.exists(legacy):
                    with open(legacy, "r", encoding="utf-8") as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        for lang, mapping in data.items():
                            if not isinstance(mapping, dict):
                                continue
                            target = self.translations.setdefault(lang, {})
                            for key, value in mapping.items():
                                target.setdefault(key, value)
        except Exception as e:
            print(f"⚠️ Failed to load translations for card {self.card_id}: {e}")

    @property
    def card_id(self) -> str:
        """Unique permanent identifier of this card."""
        return self.info.get("card_id") or self.info.get("module_id", "base_card")

    @property
    def version(self) -> str:
        """Card semantic version (e.g. '1.0.0')."""
        return str(self.info.get("version", "1.0.0"))

    @property
    def is_ready(self) -> bool:
        """
        Whether the card is ready for production/hardware testing.
        Cards under development (is_ready=False or status='wip') are disabled in
        hardware mode and require Mock Simulation Mode (use_mock=True) to run.
        """
        if "is_ready" in self.info:
            return bool(self.info["is_ready"])
        status = str(self.info.get("status", "stable")).lower()
        if status in ("wip", "dev", "development", "draft", "experimental"):
            return False
        return True

    def is_available(self, use_mock: bool = False) -> bool:
        """True if ready, or if running in mock simulation mode."""
        return self.is_ready or bool(use_mock)

    def get_criteria_summary(self, criteria: Dict[str, Any]) -> str:
        """
        Brief one-line summary of key configurable parameters,
        shown on the card when pending or running.
        Can be overridden by subclasses.
        """
        return ""

    # Backward compatibility alias
    @property
    def module_id(self) -> str:
        return self.card_id

    @property
    def card_type(self) -> str:
        """
        Either "test" or "config", declared by the card via info["card_type"] or info["module_type"].
        """
        declared = self.info.get("card_type") or self.info.get("module_type")
        if declared:
            return str(declared)
        cid = self.card_id
        if cid.startswith("config_"):
            return "config"
        return "test"

    # Backward compatibility alias
    @property
    def module_type(self) -> str:
        return self.card_type

    @property
    def priority(self) -> int:
        """
        Top-of-dashboard sorting priority (lower numbers sit higher on dashboard).
        Power providers: 10
        Serial interfaces: 20
        Other config instruments: 30
        Test cards: 100
        """
        if "priority" in self.info:
            return int(self.info["priority"])
        if self.card_type == "config":
            if getattr(self, "needs_ppk", False) or "ppk" in self.card_id:
                return 10
            return 20
        return 100

    @property
    def aliases(self) -> List[str]:
        """
        Former card_ids / module_ids, declared when a card is renamed.
        """
        raw = self.info.get("aliases") or []
        if isinstance(raw, str):
            raw = [raw]
        return [str(x) for x in raw if str(x) and str(x) != self.card_id]

    @property
    def tags(self) -> List[str]:
        """Free-form keywords used to find the card in the palette."""
        return self._tag_list(self.info.get("tags"))

    def localized_tags(self, lang: str = "en") -> List[str]:
        merged = list(self.tags)
        for key in (lang, "en"):
            source = self.translations.get(key) if self.translations else None
            if isinstance(source, dict):
                merged.extend(self._tag_list(source.get("tags")))

        seen = set()
        unique: List[str] = []
        for tag in merged:
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                unique.append(tag)
        return unique

    def all_tags(self) -> List[str]:
        merged = list(self.tags)
        for mapping in (self.translations or {}).values():
            if isinstance(mapping, dict):
                merged.extend(self._tag_list(mapping.get("tags")))

        seen = set()
        unique: List[str] = []
        for tag in merged:
            lowered = tag.lower()
            if lowered not in seen:
                seen.add(lowered)
                unique.append(tag)
        return unique

    @staticmethod
    def _tag_list(raw) -> List[str]:
        if not raw:
            return []
        if isinstance(raw, str):
            raw = [part for part in raw.replace(",", " ").split()]
        return [str(x).strip() for x in raw if str(x).strip()]

    def matches_search(self, query: str, lang: str = "en") -> bool:
        query = (query or "").strip().lower()
        if not query:
            return True
        haystack = " ".join([
            self.card_id,
            self.get_localized("name", lang),
            self.get_localized("description", lang),
            self.get_localized("name", "en"),
            self.get_localized("description", "en"),
            " ".join(self.all_tags()),
        ]).lower()
        return all(word in haystack for word in query.split())

    @property
    def icon(self) -> str:
        return self.info.get("icon", "fa-vial")

    @property
    def color(self) -> str:
        return self.info.get("color", "#3b82f6")

    @property
    def requires_serial(self) -> bool:
        return self.info.get("requires_serial", True)

    @property
    def needs_serial(self) -> bool:
        if "needs_serial" in self.capabilities:
            return bool(self.capabilities["needs_serial"])
        return self.requires_serial

    @property
    def needs_ppk(self) -> bool:
        return bool(self.capabilities.get("needs_ppk", False))

    @property
    def allows_pre_serial_cmd(self) -> bool:
        if "pre_serial_cmd" in self.capabilities:
            return bool(self.capabilities["pre_serial_cmd"])
        return self.needs_serial

    def get_serial(self, criteria: Dict[str, Any], required: bool = True) -> Optional[SerialSession]:
        session = session_from_criteria(criteria)
        if session is None and required:
            raise SerialSessionError(
                f"Card '{self.card_id}' requires a shared serial session but none was injected. "
                f"Declare capabilities = {{'needs_serial': True}} and run it through the framework."
            )
        return session

    def get_ppk(self, criteria: Dict[str, Any], required: bool = True) -> Optional[PPKSession]:
        session = ppk_session_from_criteria(criteria)
        if session is None and required:
            raise PPKSessionError(
                f"Card '{self.card_id}' requires the shared PPK2 session but none "
                f"was injected. Declare capabilities = {{'needs_ppk': True}} and run it "
                f"through the framework."
            )
        return session

    def get_instrument(self, name: str, criteria: Dict[str, Any], required: bool = True) -> Any:
        """
        Generalized instrument session retriever.
        Dispatches to get_serial, get_ppk, or custom instrument registered in criteria.
        """
        name_lower = name.lower()
        if name_lower in ("serial", "uart"):
            return self.get_serial(criteria, required=required)
        if name_lower in ("ppk", "ppk2"):
            return self.get_ppk(criteria, required=required)

        instruments = criteria.get("_instruments", {})
        inst = instruments.get(name) or criteria.get(f"_{name}")
        if inst is None and required:
            raise RuntimeError(f"Card '{self.card_id}' requires shared instrument '{name}' but none was injected.")
        return inst

    # Baud rate this card's protocol requires, or None
    required_baudrate: Optional[int] = None

    def cancelled(self, criteria: Dict[str, Any]) -> bool:
        check = criteria.get(CANCEL_CRITERIA_KEY)
        if not callable(check):
            return False
        try:
            return bool(check())
        except Exception:
            return False

    def cancel_check(self, criteria: Dict[str, Any]) -> Callable[[], bool]:
        return lambda: self.cancelled(criteria)

    def get_extra_serial(
        self,
        port: str,
        baudrate: int = 19200,
        use_mock: bool = False,
        log_callback: Optional[Callable[[str], None]] = None,
        assert_dtr_rts: bool = True,
    ) -> Optional[SerialSession]:
        port = str(port or "").strip()
        if not port:
            return None
        return serial_registry.acquire(
            port=port,
            baudrate=int(baudrate),
            mock=bool(use_mock),
            assert_dtr_rts=bool(assert_dtr_rts),
            log_callback=log_callback,
            role="extra",
        )

    def get_localized(self, field: str, lang: str = "en") -> str:
        if self.translations and lang in self.translations and field in self.translations[lang]:
            return self.translations[lang][field]
        if self.translations and "en" in self.translations and field in self.translations["en"]:
            return self.translations["en"][field]

        lang_dict = self.info.get(lang, {})
        if field in lang_dict:
            return lang_dict[field]
        en_dict = self.info.get("en", {})
        if field in en_dict:
            return en_dict[field]
        return getattr(self, field, "")

    def get_criteria_label(self, key: str, lang: str = "en") -> str:
        if self.translations and lang in self.translations:
            crit = self.translations[lang].get("criteria", {})
            if key in crit:
                return crit[key]
        if self.translations and "en" in self.translations:
            crit = self.translations["en"].get("criteria", {})
            if key in crit:
                return crit[key]
        if key in self.default_criteria and "label" in self.default_criteria[key]:
            return self.default_criteria[key]["label"]
        return key

    def get_criteria_help(self, key: str, lang: str = "en") -> str:
        for candidate in (lang, "en"):
            if self.translations and candidate in self.translations:
                helps = self.translations[candidate].get("criteria_help", {})
                if isinstance(helps, dict) and key in helps:
                    return str(helps[key])
        spec = self.default_criteria.get(key, {})
        if isinstance(spec, dict):
            return str(spec.get("help", "") or "")
        return ""

    def get_tr(self, key: str, lang: str = "en", default: str = "") -> str:
        if self.translations and lang in self.translations and key in self.translations[lang]:
            return self.translations[lang][key]
        if self.translations and "en" in self.translations and key in self.translations["en"]:
            return self.translations["en"][key]
        return default or key

    @abstractmethod
    def run(self, criteria: Dict[str, Any], use_mock: bool = False) -> Dict[str, Any]:
        """
        Execute the test logic.

        :param criteria: user-defined threshold criteria values
        :param use_mock: simulate the hardware instead of driving it.
        :return: standardised result dict:
                 {
                     "result": "PASS" | "FAIL",
                     "summary_text": str,
                     "details": { "logs": [...], "metrics": {...}, "chart": ... }
                 }
        """
        pass

    def get_info(self, lang: str = "en") -> Dict[str, Any]:
        return {
            "card_id": self.card_id,
            "module_id": self.card_id,
            "name": self.get_localized("name", lang),
            "description": self.get_localized("description", lang),
            "icon": self.icon,
            "help_text": self.get_localized("help_text", lang),
            "help_image_path": self.help_image_path,
            "requires_serial": getattr(self, "requires_serial", True),
            "needs_serial": self.needs_serial,
            "capabilities": dict(self.capabilities),
            "default_criteria": self.default_criteria,
            "priority": self.priority,
        }


# Full backward-compatibility alias
BaseTestModule = BaseCard
