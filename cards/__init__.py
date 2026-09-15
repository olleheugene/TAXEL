"""
cards package containing test and configuration cards.
"""

import sys

# Provide backwards compatibility for any external code importing `modules`
if "modules" not in sys.modules:
    sys.modules["modules"] = sys.modules[__name__]
