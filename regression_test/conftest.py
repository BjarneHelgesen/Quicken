"""
Pytest configuration for regression tests.
Imports fixtures from unit_tests/conftest.py
"""
import sys
from pathlib import Path

# Add parent directory to Python path
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

# Import unit test fixtures - regression tests use the same fixtures
from unit_tests.conftest import *  # noqa: F401, F403
