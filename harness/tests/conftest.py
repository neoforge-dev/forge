"""Pytest configuration for forge-harness iOS tests."""

import sys
from pathlib import Path

# Add the forge_harness package to the path
harness_root = Path(__file__).parent.parent
sys.path.insert(0, str(harness_root))
