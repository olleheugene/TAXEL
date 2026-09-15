"""
Shared library package.

`dtm.py` imports its sibling by top-level name (`import dtm_common`), the way the
original Nordic code does, so this package folder has to be on sys.path. Handling
that once here makes the source (.py) and the compiled binary (.so/.pyd) behave
identically.

Binary distribution:
    build_binaries.py produces .so/.pyd files carrying an ABI tag in the name
    (dtm.cpython-310-darwin.so, dtm.cp310-win_amd64.pyd, and so on). Binaries for
    several platforms can sit in the same folder: each Python loads only its own
    and falls back to the .py source when none matches.

Usage:
    from library import DTM, dtm_common, is_binary_build
"""

import importlib
import os
import sys

LIBRARY_DIR = os.path.dirname(os.path.abspath(__file__))

# Put this folder on sys.path so that dtm.py's `import dtm_common` resolves.
if LIBRARY_DIR not in sys.path:
    sys.path.insert(0, LIBRARY_DIR)

dtm_common = importlib.import_module("dtm_common")
_dtm_module = importlib.import_module("dtm")

DTM = _dtm_module.DTM
DTMError = _dtm_module.DTMError
TimeOutException = _dtm_module.TimeOutException
ConnectionError = _dtm_module.ConnectionError  # noqa: A001 - keeps the original name
MessageError = _dtm_module.MessageError


def module_origin(name: str = "dtm") -> str:
    """Path of the implementation actually loaded, to tell source from binary."""
    module = sys.modules.get(name)
    return getattr(module, "__file__", "") or ""


def is_binary_build(name: str = "dtm") -> bool:
    """Whether a compiled extension (.so/.pyd) was loaded."""
    return module_origin(name).endswith((".so", ".pyd"))


def build_info() -> dict:
    """Diagnostics: which implementation was loaded, at a glance."""
    return {
        name: {
            "origin": os.path.basename(module_origin(name)),
            "binary": is_binary_build(name),
        }
        for name in ("dtm", "dtm_common")
    }


__all__ = [
    "DTM",
    "DTMError",
    "TimeOutException",
    "ConnectionError",
    "MessageError",
    "dtm_common",
    "module_origin",
    "is_binary_build",
    "build_info",
    "LIBRARY_DIR",
]
