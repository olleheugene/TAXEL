"""
Card discovery and dynamic loading.

Walks cards/ and modules/ for card_* / test_* / config_* or <folder_name>.py
files and instantiates every BaseCard subclass found. Loads plain .py as well
as Cython-compiled .pyd / .so extensions.
"""

# Running this file directly (an IDE's Run/Debug button, or `python3 <this file>`)
# makes sys.path[0] this folder rather than the project root, so importing `core`,
# `modules` or `ui_qt` fails with ModuleNotFoundError. This guard fires only in
# that case - on a normal import __package__ is set and nothing happens here.
if __package__ in (None, ""):  # pragma: no cover - import-path bootstrap
    import os as _os
    import sys as _sys

    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))

import importlib.machinery
import importlib.util
import inspect
import os
import sys
from typing import Any, Dict, List, Optional, Tuple

from cards.base_card import BaseCard, BaseTestModule

CARD_FILE_PREFIXES = ("card_", "test_", "config_")
MODULE_FILE_PREFIXES = CARD_FILE_PREFIXES
BINARY_EXTENSIONS = (".pyd", ".so")

# Recognised file suffixes.
#   source   : .py
#   binary   : .pyd / .so, including ABI-tagged forms
#              (test_x.cpython-310-darwin.so, test_x.cp310-win_amd64.pyd)
KNOWN_SUFFIXES = tuple(
    sorted(
        set(importlib.machinery.EXTENSION_SUFFIXES) | {".py", ".pyd", ".so"},
        key=len, reverse=True,
    )
)


def split_card_filename(filename: str) -> Tuple[str, Optional[str]]:
    """
    Split a filename into (card name, suffix). Files this platform cannot
    load come back with a None suffix and the caller skips them quietly.
    """
    for suffix in KNOWN_SUFFIXES:
        if not filename.endswith(suffix):
            continue
        stem = filename[: -len(suffix)]
        if suffix in BINARY_EXTENSIONS and "." in stem:
            return stem.split(".", 1)[0], None
        return stem, suffix
    return filename, None


split_module_filename = split_card_filename


def build_alias_map(cards: Dict[str, Any]) -> Dict[str, str]:
    """
    Build a {former card_id: current card_id} mapping.
    An alias that collides with a real card_id is ignored.
    """
    alias_map: Dict[str, str] = {}
    for card_id, card in cards.items():
        for alias in getattr(card, "aliases", []) or []:
            if alias in cards:
                continue
            alias_map.setdefault(alias, card_id)
    return alias_map


def resolve_card_id(cards: Dict[str, Any], card_id: str) -> Optional[str]:
    """
    Resolve a stored id to the current one, or None if unknown.
    Honours aliases across former names left in dashboards, recipes and trace logs.
    """
    if not card_id:
        return None
    if card_id in cards:
        return card_id
    return build_alias_map(cards).get(card_id)


resolve_module_id = resolve_card_id


def get_card(cards: Dict[str, Any], card_id: str) -> Optional[BaseCard]:
    """Return the card instance, honouring aliases. None if not found."""
    resolved = resolve_card_id(cards, card_id)
    return cards.get(resolved) if resolved else None


get_module = get_card


def card_search_dirs(base_dir: str) -> List[str]:
    """
    Search paths for cards/ and modules/. In a PyInstaller-frozen executable
    the folder next to the executable is checked first.
    """
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.join(os.path.dirname(sys.executable), "cards"))
        dirs.append(os.path.join(os.path.dirname(sys.executable), "modules"))
    dirs.append(os.path.join(base_dir, "cards"))
    dirs.append(os.path.join(base_dir, "modules"))

    seen = set()
    unique = []
    for d in dirs:
        real = os.path.abspath(d)
        if real not in seen:
            seen.add(real)
            unique.append(d)
    return unique


module_search_dirs = card_search_dirs


def discover_cards(
    base_dir: Optional[str] = None,
    search_dirs: Optional[List[str]] = None,
    on_error=None,
) -> Dict[str, BaseCard]:
    """
    Discover cards and return them as {card_id: instance}.

    :param base_dir: Project root directory (defaults to parent of core/)
    :param on_error: (file_path, exception) callback; prints to stdout if None.
    """
    if base_dir is None:
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    loaded: Dict[str, BaseCard] = {}

    def report(file_path: str, exc: Exception):
        if on_error:
            on_error(file_path, exc)
        else:
            print(f"❌ Failed to load external card ({file_path}): {exc}")

    # 1) Collect candidate files. A card may ship both source and binary, so
    #    pick exactly one per stem (the name before the first dot).
    candidates: Dict[str, Dict[str, Any]] = {}

    for search_dir in (search_dirs if search_dirs is not None else card_search_dirs(base_dir)):
        if not os.path.exists(search_dir):
            continue

        for root, _dirs, files in os.walk(search_dir):
            parent_folder = os.path.basename(root)
            for filename in files:
                stem, suffix = split_card_filename(filename)
                if suffix is None:
                    continue

                # Acceptable filename patterns:
                # 1. Starts with CARD_FILE_PREFIXES ("card_", "test_", "config_")
                # 2. Or matches the parent folder name (e.g. my_check/my_check.py)
                is_recognized_name = (
                    filename.startswith(CARD_FILE_PREFIXES) or
                    stem == parent_folder
                )
                if not is_recognized_name:
                    continue

                is_binary = suffix.endswith(BINARY_EXTENSIONS)
                existing = candidates.get(stem)
                # Prefer binary over source. For same kind, keep the first found.
                if existing is None or (is_binary and not existing["is_binary"]):
                    candidates[stem] = {
                        "path": os.path.join(root, filename),
                        "is_binary": is_binary,
                    }

    # 2) Load them.
    for stem, info in candidates.items():
        file_path = info["path"]
        try:
            card_module_name = stem if info["is_binary"] else f"ext_card_{stem}"
            spec = importlib.util.spec_from_file_location(card_module_name, file_path)
            if not (spec and spec.loader):
                continue
            mod = importlib.util.module_from_spec(spec)
            if info["is_binary"]:
                sys.modules[card_module_name] = mod
            spec.loader.exec_module(mod)
            for _, obj in inspect.getmembers(mod, inspect.isclass):
                if not issubclass(obj, BaseCard) or obj in (BaseCard, BaseTestModule):
                    continue
                owner = str(obj.__module__).rsplit(".", 1)[-1]
                if owner not in (stem, card_module_name):
                    continue
                instance = obj()
                loaded[instance.card_id] = instance
        except Exception as e:
            report(file_path, e)

    return loaded


discover_modules = discover_cards
