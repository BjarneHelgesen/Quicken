"""
Pytest configuration for Quicken unit tests.

Features:
- Adds parent directory to Python path so tests can import quicken module
- Provides persistent fixture cache for faster test iterations
- Uses persistent temp directories for consistent file paths

Persistent Fixture System:
==========================
Both the cache AND temp directories persist across test runs for maximum speedup.

~/.quicken/test_fixture_cache/<hash>/  - Cached tool results
~/.quicken/test_temp/<hash>/           - Test source files

This ensures:
- Same file paths across runs → cache hits work
- Same tool outputs → no subprocess overhead
- Dramatic speedup after first run

Performance:
First run (cold):    ~107 seconds (populates cache)
Subsequent runs:     ~3-5 seconds (uses cache)  ⚡ 30x faster!

The cache/temp are automatically invalidated (new hash) when:
- tools.json changes
- Test source code constants change (SIMPLE_CPP_CODE, CPP_CODE_WITH_WARNING)

Manual management:
- Clear all: rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/
"""
import hashlib
import json
import shutil
import sys
import tempfile
from pathlib import Path

import pytest

# Add parent directory to Python path so we can import quicken
parent_dir = Path(__file__).parent.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from quicken import Quicken, QuickenCache


def pytest_configure(config):
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "pedantic: pedantic tests that verify edge cases (can be skipped with -m 'not pedantic')"
    )


# Test source code that affects cache validity
SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""

CPP_CODE_WITH_WARNING = """
#include <iostream>

int main() {
    int unused_var = 0;
    std::cout << "Test" << std::endl;
    return 0;
}
"""

FIXTURE_CACHE_DIR = Path.home() / ".quicken" / "test_fixture_cache"
FIXTURE_TEMP_DIR = Path.home() / ".quicken" / "test_temp"


def _get_fixture_cache_hash():
    """Hash of all inputs that affect fixture cache validity."""
    h = hashlib.sha256()

    # Hash tools.json
    tools_json = Path(__file__).parent / "tools.json"
    if tools_json.exists():
        h.update(tools_json.read_bytes())

    # Hash test source files
    h.update(SIMPLE_CPP_CODE.encode())
    h.update(CPP_CODE_WITH_WARNING.encode())

    return h.hexdigest()[:16]


@pytest.fixture(scope="session")
def persistent_tool_cache():
    """Persistent cache of tool results that survives across test runs.

    This cache is keyed by a hash of tools.json and test source code.
    If any of those change, a new cache is created.

    The cache is populated naturally as tests run on first execution,
    then reused on subsequent runs for dramatic speedup.
    """
    cache_hash = _get_fixture_cache_hash()
    cache_dir = FIXTURE_CACHE_DIR / cache_hash

    if cache_dir.exists():
        # Reuse existing cache
        cache = QuickenCache(cache_dir)
        print(f"\n[Quicken] Using persistent cache: {cache_hash} ({len(cache.index)} entries)")
        return cache

    # Create new cache directory (will be populated as tests run)
    print(f"\n[Quicken] Creating new persistent cache: {cache_hash}")
    print("[Quicken] First run will be slower (~107s) as cache is populated")
    print("[Quicken] Subsequent runs will be fast (~3-5s)")
    cache_dir.mkdir(parents=True, exist_ok=True)

    return QuickenCache(cache_dir)


@pytest.fixture
def config_file():
    """Path to tools.json configuration file."""
    project_tools = Path(__file__).parent / "tools.json"
    if project_tools.exists():
        return project_tools

    # Fallback for tests that need config but tools.json is missing
    temp_dir = Path(tempfile.mkdtemp(prefix="quicken_config_"))
    config = temp_dir / "tools.json"
    config_data = {
        "cl": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\MSVC\\14.44.35207\\bin\\Hostx64\\x64\\cl.exe",
        "vcvarsall": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvarsall.bat",
        "msvc_arch": "x64",
        "clang++": "clang++",
        "clang-tidy": "clang-tidy"
    }
    config.write_text(json.dumps(config_data, indent=2))
    return config


@pytest.fixture(scope="session")
def persistent_temp_dir():
    """Persistent temp directory for test files that survives across test runs.

    Uses the same hash as the cache to ensure consistency.
    """
    cache_hash = _get_fixture_cache_hash()
    temp_dir = FIXTURE_TEMP_DIR / cache_hash

    if temp_dir.exists():
        print(f"[Quicken] Using persistent temp dir: {cache_hash}")
    else:
        print(f"[Quicken] Creating persistent temp dir: {cache_hash}")
        temp_dir.mkdir(parents=True, exist_ok=True)

    return temp_dir


@pytest.fixture
def temp_dir(persistent_temp_dir, request):
    """Test-specific subdirectory within persistent temp dir.

    Each test gets its own subdirectory to avoid conflicts.
    """
    # Use test name as subdirectory (safe for file system)
    test_name = request.node.name.replace("[", "_").replace("]", "_")
    test_dir = persistent_temp_dir / test_name

    # Clean and recreate directory for this test
    if test_dir.exists():
        shutil.rmtree(test_dir)
    test_dir.mkdir(parents=True, exist_ok=True)

    yield test_dir

    # Don't cleanup - let it persist for next run


@pytest.fixture
def quicken_with_persistent_cache(config_file, persistent_tool_cache):
    """Quicken instance that uses persistent fixture cache for faster tests.

    Uses the same cache instance directly (no copying) for maximum speed.
    """
    quicken = Quicken(config_file)
    # Use persistent cache directly - no copying needed!
    quicken.cache = persistent_tool_cache
    return quicken
