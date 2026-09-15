#!/usr/bin/env python3
"""
📦 Build script for binary extension modules (.so / .pyd)

Compiles Python sources into C extensions with Cython, for closed-source
distribution and faster module loading.

  library/   shared libraries (dtm.py, dtm_common.py, ...)
  modules/   test modules (test_*.py, config_*.py) - subfolders included

────────────────────────────────────────────────────────────────────────────
⚠️  Cross-compilation is not possible
────────────────────────────────────────────────────────────────────────────
A C extension is a different file for every (OS x CPU architecture x Python
version). A Windows .pyd cannot be produced on macOS, so this script has to be
run once on each OS.

Fortunately **binaries for several platforms may share one folder.** The ABI tag
in the filename lets each Python pick out only its own.

    library/
      dtm.cpython-310-darwin.so        <- loaded by macOS Python 3.10
      dtm.cp310-win_amd64.pyd          <- loaded by Windows Python 3.10
      dtm.cpython-310-x86_64-linux-gnu.so
      dtm.py                           <- fallback only when none of the above match

So the artefacts built on each OS can simply be gathered into one folder for
distribution.

Usage:
    python3 build_binaries.py                  # build library + modules
    python3 build_binaries.py --targets library
    python3 build_binaries.py --list           # show targets and current state
    python3 build_binaries.py --collect        # gather artefacts into dist/<platform>/
    python3 build_binaries.py --clean          # remove binaries and intermediate .c
    python3 build_binaries.py --strip-sources  # (careful) drop .py sources from a build

Display strings come from the language files; set NRF_LANG to pick a language.
"""

import argparse
import glob
import json
import os
import shutil
import subprocess
import sys
import sysconfig

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

# Display strings come from the language files. core.language has no Qt dependency,
# so a build script can use it directly.
from core.language import language  # noqa: E402

# Pick the language with an environment variable, e.g. NRF_LANG=ja
language.set_language(os.environ.get("NRF_LANG", "en"))
tr = language.tr

# Build targets: name -> (folder, file patterns)
TARGETS = {
    "library": ("library", ["*.py"]),
    "cards": ("cards", ["*/test_*.py", "*/config_*.py", "test_*.py", "config_*.py", "*/card_*.py", "card_*.py"]),
    "modules": ("cards", ["*/test_*.py", "*/config_*.py", "test_*.py", "config_*.py", "*/card_*.py", "card_*.py"]),
}

# Files that are never compiled. base_module and serial_session are the shared
# contract cards import, and keeping them as source helps debugging and
# third-party development.
EXCLUDE_NAMES = {"__init__.py", "setup.py", "base_module.py", "base_card.py", "serial_session.py", "ppk_session.py", "dtm_base.py", "dtm_companion.py"}

BINARY_SUFFIXES = (".so", ".pyd")


def platform_tag() -> str:
    """Platform tag used to separate artefacts, e.g. cp310-macosx-11.1-arm64."""
    impl = f"cp{sys.version_info.major}{sys.version_info.minor}"
    return f"{impl}-{sysconfig.get_platform()}"


def ext_suffix() -> str:
    return sysconfig.get_config_var("EXT_SUFFIX") or (".pyd" if os.name == "nt" else ".so")


def check_cython() -> bool:
    try:
        import Cython  # noqa: F401
        return True
    except ImportError:
        print(tr("build_no_cython"))
        print("   pip install cython setuptools")
        return False


def collect_sources(target_names) -> list:
    """Gather the .py paths to compile, relative to the project root."""
    sources = []
    for name in target_names:
        folder, patterns = TARGETS[name]
        root = os.path.join(BASE_DIR, folder)
        if not os.path.isdir(root):
            print(tr("build_no_target_dir", folder=folder))
            continue
        for pattern in patterns:
            for path in sorted(glob.glob(os.path.join(root, pattern))):
                if os.path.basename(path) in EXCLUDE_NAMES:
                    continue
                rel = os.path.relpath(path, BASE_DIR)
                if rel not in sources:
                    sources.append(rel)
    return sources


def existing_binaries(target_names) -> dict:
    """Return the binaries already present as {folder: [filename, ...]}."""
    found = {}
    for name in target_names:
        folder, _ = TARGETS[name]
        root = os.path.join(BASE_DIR, folder)
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(BINARY_SUFFIXES):
                    rel = os.path.relpath(os.path.join(dirpath, f), BASE_DIR)
                    key = os.path.dirname(rel)
                    found.setdefault(key, []).append(os.path.basename(rel))
    return found


def find_stale(target_names) -> list:
    """
    Find entries whose source is newer than its binary.

    Binaries load ahead of sources, so editing a source without rebuilding runs
    the old code silently. This is the single biggest time sink when debugging.
    """
    suffix = ext_suffix()
    stale = []
    for rel in collect_sources(target_names):
        src = os.path.join(BASE_DIR, rel)
        binary = os.path.splitext(src)[0] + suffix
        if not os.path.exists(binary):
            continue
        if os.path.getmtime(src) > os.path.getmtime(binary):
            stale.append(rel)
    return stale


def warn_if_stale(target_names) -> None:
    stale = find_stale(target_names)
    if not stale:
        return
    print("\n" + tr("build_stale_header"))
    print(tr("build_stale_hint"))
    for rel in stale:
        print(f"      {rel}")
    print(tr("build_rebuild_cmd"))


def do_list(target_names) -> int:
    print(tr("build_platform_tag", tag=platform_tag()))
    print(tr("build_ext_suffix", suffix=ext_suffix()))
    print()

    sources = collect_sources(target_names)
    print(tr("build_targets", count=len(sources)))
    for rel in sources:
        print(f"  {rel}")

    binaries = existing_binaries(target_names)
    print("\n" + tr("build_existing"))
    if not binaries:
        print(tr("build_none"))
    for folder in sorted(binaries):
        for name in sorted(binaries[folder]):
            print(f"  {os.path.join(folder, name)}")

    warn_if_stale(target_names)
    return 0


def do_clean(target_names) -> int:
    removed = 0
    for name in target_names:
        folder, _ = TARGETS[name]
        root = os.path.join(BASE_DIR, folder)
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if f.endswith(BINARY_SUFFIXES) or f.endswith(".c"):
                    path = os.path.join(dirpath, f)
                    os.remove(path)
                    print(f"  🗑  {os.path.relpath(path, BASE_DIR)}")
                    removed += 1
    build_dir = os.path.join(BASE_DIR, "build")
    if os.path.isdir(build_dir):
        shutil.rmtree(build_dir)
        print("  🗑  build/")
    print("\n" + tr("build_removed", count=removed))
    return 0


def do_build(target_names, quiet: bool) -> int:
    if not check_cython():
        return 1

    sources = collect_sources(target_names)
    if not sources:
        print(tr("build_nothing"))
        return 1

    print(tr("build_start", tag=platform_tag()))
    print(tr("build_targets_short", count=len(sources)))
    for rel in sources:
        print(f"   {rel}")

    # Derive the extension module name from the file path.
    #
    # Module folders are not Python packages (no __init__.py), so without an
    # explicit name Cython uses just the filename and --inplace drops the
    # artefact in the project root. A dotted name lets setuptools work out the
    # path and place it next to the source.
    #
    # Important: the last component of this name determines PyInit_<name>, and
    # the framework loader (core.registry) loads the binary under that name.
    extensions = []
    for rel in sources:
        stem = os.path.splitext(rel)[0]
        dotted = stem.replace(os.sep, ".").replace("/", ".")
        extensions.append((dotted, rel))

    setup_code = (
        "from setuptools import setup, Extension\n"
        "from Cython.Build import cythonize\n"
        f"SPECS = {extensions!r}\n"
        "ext_modules = [Extension(name, [path]) for name, path in SPECS]\n"
        "setup(ext_modules=cythonize(\n"
        "    ext_modules,\n"
        "    compiler_directives={'language_level': '3'},\n"
        "    quiet=True,\n"
        "))\n"
    )
    setup_path = os.path.join(BASE_DIR, "_temp_setup.py")
    with open(setup_path, "w", encoding="utf-8") as f:
        f.write(setup_code)

    try:
        cmd = [sys.executable, "_temp_setup.py", "build_ext", "--inplace"]
        print(f"\n🔨 {' '.join(cmd)}")
        res = subprocess.run(
            cmd, cwd=BASE_DIR,
            stdout=subprocess.DEVNULL if quiet else None,
            stderr=None,
        )
        if res.returncode != 0:
            print("\n" + tr("build_failed"))
            return res.returncode

        print("\n" + tr("build_done", suffix=ext_suffix()))
        binaries = existing_binaries(target_names)
        total = sum(len(v) for v in binaries.values())
        print(tr("build_count", count=total))
        print("\n" + tr("build_other_os"))
        print(tr("build_other_os_hint"))
        return 0
    finally:
        if os.path.exists(setup_path):
            os.remove(setup_path)
        build_dir = os.path.join(BASE_DIR, "build")
        if os.path.isdir(build_dir):
            shutil.rmtree(build_dir)
        # Clean up the intermediate .c files Cython leaves behind
        for rel in sources:
            c_file = os.path.join(BASE_DIR, os.path.splitext(rel)[0] + ".c")
            if os.path.exists(c_file):
                os.remove(c_file)


def do_collect(target_names) -> int:
    """
    Copy this platform's artefacts into dist/<platform tag>/.
    Build on each OS, then merge those folders for distribution.
    """
    tag = platform_tag()
    out_root = os.path.join(BASE_DIR, "dist", tag)
    copied = []

    for name in target_names:
        folder, _ = TARGETS[name]
        root = os.path.join(BASE_DIR, folder)
        for dirpath, _dirs, files in os.walk(root):
            for f in files:
                if not f.endswith(BINARY_SUFFIXES):
                    continue
                src = os.path.join(dirpath, f)
                rel = os.path.relpath(src, BASE_DIR)
                dst = os.path.join(out_root, rel)
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(src, dst)
                copied.append(rel)

    if not copied:
        print(tr("build_nothing_collect"))
        return 1

    manifest = {
        "platform_tag": tag,
        "python": sys.version.split()[0],
        "ext_suffix": ext_suffix(),
        "sysconfig_platform": sysconfig.get_platform(),
        "files": sorted(copied),
    }
    with open(os.path.join(out_root, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)

    print(tr("build_collected", count=len(copied), tag=tag))
    for rel in sorted(copied):
        print(f"   {rel}")
    print("\n" + tr("build_manifest"))
    return 0


def do_strip_sources(target_names) -> int:
    """
    Remove .py sources that have a matching binary. Use only when producing a
    distribution. Files without a binary are left alone, so the build never ends
    up unrunnable.
    """
    suffix = ext_suffix()
    removed, kept = [], []
    for rel in collect_sources(target_names):
        src = os.path.join(BASE_DIR, rel)
        binary = os.path.splitext(src)[0] + suffix
        if os.path.exists(binary):
            os.remove(src)
            removed.append(rel)
        else:
            kept.append(rel)

    for rel in removed:
        print(f"  🗑  {rel}")
    if kept:
        print("\n" + tr("build_kept_sources", count=len(kept)))
        for rel in kept:
            print(f"   {rel}")
    print("\n" + tr("build_stripped", count=len(removed)))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build Cython binary extensions (.so / .pyd)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--targets", nargs="+", choices=sorted(TARGETS), default=sorted(TARGETS),
        help=tr("build_arg_targets"),
    )
    parser.add_argument("--list", action="store_true", help=tr("build_arg_list"))
    parser.add_argument("--clean", action="store_true", help=tr("build_arg_clean"))
    parser.add_argument("--collect", action="store_true", help=tr("build_arg_collect"))
    parser.add_argument("--strip-sources", action="store_true", help=tr("build_arg_strip"))
    parser.add_argument("--check-stale", action="store_true", help=tr("build_arg_check_stale"))
    parser.add_argument("--quiet", action="store_true", help=tr("build_arg_quiet"))
    args = parser.parse_args()

    if args.check_stale:
        stale = find_stale(args.targets)
        if stale:
            warn_if_stale(args.targets)
            return 1
        print(tr("build_up_to_date"))
        return 0
    if args.list:
        return do_list(args.targets)
    if args.clean:
        return do_clean(args.targets)
    if args.collect:
        return do_collect(args.targets)
    if args.strip_sources:
        return do_strip_sources(args.targets)
    return do_build(args.targets, args.quiet)


if __name__ == "__main__":
    sys.exit(main())
