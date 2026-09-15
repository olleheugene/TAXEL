"""
Translation manager.

Translation files are kept **one per language**.

    language/
      en.json      { "app_title": "...", "btn_run_all": "...", ... }
      ko.json
      ja.json

Dropping in one more file makes that language appear in the language menu - no
code change needed. It also means adding a language never involves editing one
huge shared file and resolving conflicts in it.

Module-bundled translations work the same way (modules/<id>/language/<lang>.json).
The older nested format (a single translations.json holding every language) is
still read, because already-deployed modules must keep working. When the same
key exists in both, the per-language file wins.
"""

import glob
import json
import os
import re
from typing import Dict, List, Optional, Tuple

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

LANGUAGE_DIR_NAME = "language"
LEGACY_FILE_NAME = "translations.json"

# A language code as it appears as a filename or as a key in the nested format.
LANG_CODE_RE = re.compile(r"^[a-z]{2,3}(?:[_-][A-Za-z0-9]{2,4})?$")

LANG_DISPLAY_NAMES = {
    "en": "English (en)",
    "ko": "한국어 (ko)",
    "ja": "日本語 (ja)",
    "zh": "中文 (zh)",
    "de": "Deutsch (de)",
    "fr": "Français (fr)",
    "es": "Español (es)",
}


def _read_json(path: str) -> Optional[dict]:
    """Missing file -> silently None. Present but unreadable -> report it, so a
    malformed file is never hidden."""
    if not os.path.isfile(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except json.JSONDecodeError as e:
        print(f"⚠️ Invalid JSON in {path}: {e}")
    except Exception as e:
        print(f"⚠️ Failed to read {path}: {e}")
    return None


def _looks_nested_by_language(data: dict) -> bool:
    """
    True when a file holds every language nested under a language code, i.e. the
    old translations.json shape rather than a per-language file.

    Checking only "does it contain a dict" is not enough: a module's
    per-language file legitimately nests "criteria" and "criteria_help".
    A nested-by-language file is recognised by *every* top-level key looking
    like a language code and *every* value being a mapping.
    """
    if not data:
        return False
    return all(
        isinstance(value, dict) and LANG_CODE_RE.match(str(key))
        for key, value in data.items()
    )


def load_language_dir(directory: str) -> Dict[str, Dict[str, str]]:
    """
    Read translations from one directory and return {lang: {key: text}}.

    Per-language files (<lang>.json) take precedence; the nested format
    (translations.json) only fills in the gaps.
    """
    result: Dict[str, Dict[str, str]] = {}
    if not os.path.isdir(directory):
        return result

    # 1) Per-language files - highest precedence
    for path in sorted(glob.glob(os.path.join(directory, "*.json"))):
        lang = os.path.splitext(os.path.basename(path))[0]
        if lang == os.path.splitext(LEGACY_FILE_NAME)[0]:
            continue  # the nested format is handled separately below
        data = _read_json(path)
        if not data:
            continue
        if _looks_nested_by_language(data):
            # A nested-by-language file was dropped in under a language name by
            # mistake. Do not swallow that: the keys would end up wrong.
            print(f"⚠️ {path} looks like a nested translation file. "
                  f"A per-language file must map keys directly to text.")
            continue
        # Values are usually strings, but a module file legitimately holds
        # sub-dicts ("criteria", "criteria_help") and lists ("tags"). Keep both
        # structures intact - str() on a list would turn it into the literal
        # "['a', 'b']" and every consumer would then have to parse it back.
        # tr() only ever returns string values.
        merged = result.setdefault(lang, {})
        for key, value in data.items():
            merged[str(key)] = value if isinstance(value, (dict, list)) else str(value)

    # 2) Nested format - backwards compatible. Never overrides existing keys.
    legacy = _read_json(os.path.join(directory, LEGACY_FILE_NAME))
    if legacy:
        for lang, mapping in legacy.items():
            if not isinstance(mapping, dict):
                continue
            target = result.setdefault(lang, {})
            for key, value in mapping.items():
                if isinstance(value, (str, int, float)):
                    target.setdefault(str(key), str(value))
                else:
                    # Pass module-style nested values (a criteria sub-dict,
                    # for example) straight through.
                    target.setdefault(str(key), value)

    return result


class LanguageManager:
    _instance = None

    def __init__(self, base_dir: Optional[str] = None):
        self.base_dir = base_dir or BASE_DIR
        self.current_lang = self._get_initial_language()
        self.translations: Dict[str, Dict[str, str]] = {}
        self.load_translations()

    def _get_initial_language(self) -> str:
        # 1. Explicit environment variable has top priority
        env_lang = os.environ.get("NRF_LANG")
        if env_lang:
            return env_lang
        # 2. Check persisted settings in dashboard_layout.json
        layout_path = os.path.join(self.base_dir, "dashboard_layout.json")
        if os.path.exists(layout_path):
            try:
                with open(layout_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    saved = data.get("settings", {}).get("language")
                    if saved and isinstance(saved, str):
                        return saved
            except Exception:
                pass
        return "en"

    @classmethod
    def get_instance(cls) -> "LanguageManager":
        if cls._instance is None:
            cls._instance = LanguageManager()
        return cls._instance

    @property
    def language_dir(self) -> str:
        return os.path.join(self.base_dir, LANGUAGE_DIR_NAME)

    def load_translations(self) -> None:
        self.translations = load_language_dir(self.language_dir)

    def reload(self) -> None:
        """Re-read the translation files, so edits apply without a restart."""
        self.load_translations()

    def available_files(self) -> List[Tuple[str, str]]:
        """List of (language code, file path). For diagnostics and tooling."""
        found = []
        for path in sorted(glob.glob(os.path.join(self.language_dir, "*.json"))):
            lang = os.path.splitext(os.path.basename(path))[0]
            if lang != os.path.splitext(LEGACY_FILE_NAME)[0]:
                found.append((lang, path))
        return found

    def discover_languages(self, loaded_modules: dict = None) -> List[str]:
        """
        Collect the available language codes from:
          1. language/<lang>.json (plus the legacy translations.json)
          2. the translations bundled with every registered module
        Returns 'en' first, then 'ko', then the rest alphabetically.
        """
        langs = set(self.translations.keys())

        if loaded_modules:
            for mod in loaded_modules.values():
                mod_translations = getattr(mod, "translations", {})
                if isinstance(mod_translations, dict):
                    langs.update(mod_translations.keys())

        langs.add("en")
        sorted_langs = list(langs)
        sorted_langs.sort(key=lambda x: (0 if x == "en" else (1 if x == "ko" else 2), x))
        return sorted_langs

    def set_language(self, lang_code: str) -> None:
        self.current_lang = lang_code

    def tr(self, key: str, **fmt) -> str:
        text = None
        current = self.translations.get(self.current_lang) or {}
        if key in current and isinstance(current[key], str):
            text = current[key]

        if text is None:
            fallback = self.translations.get("en") or {}
            if key in fallback and isinstance(fallback[key], str):
                text = fallback[key]

        if text is None:
            text = key

        if fmt:
            try:
                text = text.format(**fmt)
            except Exception:
                pass
        return text

    # ------------------------------------------------------------- diagnostics
    def missing_keys(self, reference: str = "en") -> Dict[str, List[str]]:
        """
        Find keys present in the reference language but absent elsewhere.
        With per-language files it is easy to add a key to one file and forget
        the others, so this check matters.
        """
        ref = set((self.translations.get(reference) or {}).keys())
        report: Dict[str, List[str]] = {}
        for lang, mapping in self.translations.items():
            if lang == reference:
                continue
            missing = sorted(ref - set(mapping.keys()))
            if missing:
                report[lang] = missing
        return report

    def untranslated_keys(self, reference: str = "en") -> Dict[str, List[str]]:
        """Keys whose value equals the reference language, i.e. likely left
        untranslated."""
        ref = self.translations.get(reference) or {}
        report: Dict[str, List[str]] = {}
        for lang, mapping in self.translations.items():
            if lang == reference:
                continue
            same = sorted(
                k for k, v in mapping.items()
                if k in ref and v == ref[k] and isinstance(v, str)
            )
            if same:
                report[lang] = same
        return report


language = LanguageManager.get_instance()
