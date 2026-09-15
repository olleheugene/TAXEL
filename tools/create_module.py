#!/usr/bin/env python3
"""
Backward compatibility wrapper for tools/create_card.py.
Delegates module creation to create_card.py.
"""

import sys
import os

# Ensure tools directory is in sys.path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from create_card import main

if __name__ == "__main__":
    sys.exit(main())
