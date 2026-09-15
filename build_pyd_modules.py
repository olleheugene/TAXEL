#!/usr/bin/env python3
"""
(legacy) PyD/SO compile script - superseded by build_binaries.py.

This file remains as a pointer for anyone still calling it. What the new script
does differently:
  - walks subfolders of modules/ (the old one only looked at the top level and
    therefore found nothing)
  - compiles the shared libraries in library/ as well
  - places artefacts next to their source (the old one dropped them in the
    project root)
  - supports --list / --clean / --collect / --strip-sources
"""

import subprocess
import sys
import os

if __name__ == "__main__":
    print(__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    from core.language import language
    language.set_language(os.environ.get("NRF_LANG", "en"))
    print(language.tr("build_shim_redirect") + "\n")
    sys.exit(subprocess.run(
        [sys.executable, os.path.join(here, "build_binaries.py")] + sys.argv[1:],
        cwd=here,
    ).returncode)
