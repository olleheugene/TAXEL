#!/usr/bin/env python3
"""
Translation file checker.

With one file per language it is easy to add a key to one and forget the others.
Run this before a release to catch what is missing.

    python3 tools/check_language.py
    python3 tools/check_language.py --strict     # exit 1 when keys are missing
"""

import argparse
import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from core.language import LanguageManager  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Check language language files")
    parser.add_argument("--reference", default="en", help="reference language (default: en)")
    parser.add_argument("--strict", action="store_true", help="exit 1 if keys are missing")
    args = parser.parse_args()

    manager = LanguageManager()
    # This tool's own output stays in English: a tool that inspects the state of
    # the translations should not depend on them.
    files = manager.available_files()

    print(f"language directory: {manager.language_dir}")
    if not files:
        print("❌ No per-language files found (expected language/<lang>.json)")
        return 1
    for lang, path in files:
        print(f"  {os.path.basename(path):<14} {len(manager.translations.get(lang, {})):>4} keys")

    exit_code = 0

    missing = manager.missing_keys(args.reference)
    if missing:
        print(f"\n❌ Keys present in '{args.reference}' but missing elsewhere:")
        for lang, keys in sorted(missing.items()):
            print(f"  {lang}: {len(keys)}")
            for key in keys:
                print(f"    - {key}")
        if args.strict:
            exit_code = 1
    else:
        print(f"\n✅ No missing keys (reference: {args.reference})")

    extra = {}
    ref_keys = set((manager.translations.get(args.reference) or {}).keys())
    for lang, mapping in manager.translations.items():
        if lang == args.reference:
            continue
        surplus = sorted(set(mapping.keys()) - ref_keys)
        if surplus:
            extra[lang] = surplus
    if extra:
        print(f"\n⚠️ Keys not present in '{args.reference}' (likely stale):")
        for lang, keys in sorted(extra.items()):
            print(f"  {lang}: {', '.join(keys)}")

    same = manager.untranslated_keys(args.reference)
    if same:
        print("\nℹ️ Identical to reference (often intentional: PASS/FAIL, product names, format-only):")
        for lang, keys in sorted(same.items()):
            print(f"  {lang}: {', '.join(keys)}")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
