#!/usr/bin/env python3
"""
Test that cache entries are reused when file content matches existing entries.
This prevents orphaned cache entries when files are reverted or touched without changes.
"""

import json
import shutil
import time
from pathlib import Path

import pytest

from quicken import Quicken


# Simple C++ code for testing
TEST_CPP_V1 = """
#include <iostream>

int main() {
    std::cout << "Version 1" << std::endl;
    return 0;
}
"""

TEST_CPP_V2 = """
#include <iostream>

int main() {
    std::cout << "Version 2" << std::endl;
    return 0;
}
"""


@pytest.fixture
def temp_dir(tmp_path):
    """Create a temporary directory."""
    return tmp_path


@pytest.fixture
def config_file(temp_dir):
    """Create a test config file pointing to the real tools.json."""
    # Use the actual tools.json from the project (parent directory)
    project_tools = Path(__file__).parent.parent / "tools.json"
    if project_tools.exists():
        return project_tools

    # Fallback: create a minimal config
    config = temp_dir / "tools.json"
    config_data = {
        "cl": "cl.exe",
        "vcvarsall": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvarsall.bat",
        "msvc_arch": "x64"
    }
    config.write_text(json.dumps(config_data, indent=2))
    return config


@pytest.fixture
def quicken_instance(config_file):
    """Create a Quicken instance with clean cache."""
    q = Quicken(config_file)
    q.clear_cache()
    return q


class TestCacheEntryReuse:
    """Test that cache entries are reused when file content matches."""

    def test_reuse_entry_after_revert(self, quicken_instance, temp_dir):
        """
        Verify that when a file is reverted to previous content, the existing
        cache entry is reused instead of creating a duplicate.

        Steps:
        1. Compile test.cpp with content V1 → creates entry_000001
        2. Modify test.cpp to content V2 and compile → creates entry_000002
        3. Revert test.cpp to content V1 (new mtime) and compile → should reuse entry_000001
        4. Verify only 2 cache entries exist (not 3)
        """
        test_cpp = temp_dir / "test.cpp"
        args = ['/c', '/nologo', '/EHsc']

        # Step 1: Compile V1
        test_cpp.write_text(TEST_CPP_V1)
        returncode = quicken_instance.run(test_cpp, "cl", args, repo_dir=temp_dir)
        assert returncode == 0

        # Verify entry_000001 was created
        cache_dir = Path.home() / ".quicken" / "cache"
        entry_001 = cache_dir / "entry_000001"
        assert entry_001.exists(), "entry_000001 should exist after first compilation"

        # Step 2: Modify and compile V2
        time.sleep(0.01)  # Ensure different mtime
        test_cpp.write_text(TEST_CPP_V2)
        returncode = quicken_instance.run(test_cpp, "cl", args, repo_dir=temp_dir)
        assert returncode == 0

        # Verify entry_000002 was created
        entry_002 = cache_dir / "entry_000002"
        assert entry_002.exists(), "entry_000002 should exist after second compilation"

        # Step 3: Revert to V1 and compile
        time.sleep(0.01)  # Ensure different mtime
        test_cpp.write_text(TEST_CPP_V1)
        returncode = quicken_instance.run(test_cpp, "cl", args, repo_dir=temp_dir)
        assert returncode == 0

        # Step 4: Verify entry_000003 does NOT exist (entry_000001 was reused)
        entry_003 = cache_dir / "entry_000003"
        assert not entry_003.exists(), "entry_000003 should NOT exist - entry_000001 should be reused"

        # Verify only 2 entries exist
        cache_entries = [d for d in cache_dir.iterdir() if d.is_dir() and d.name.startswith("entry_")]
        assert len(cache_entries) == 2, f"Expected 2 cache entries, found {len(cache_entries)}"

        # Verify index points to entry_000001 for the current configuration
        index_file = cache_dir / "index.json"
        with open(index_file, 'r') as f:
            index = json.load(f)

        # Find the compound key for our test
        compound_key = None
        for key in index.keys():
            if "test.cpp" in key and "cl" in key:
                compound_key = key
                break

        assert compound_key is not None, "Compound key for test.cpp should exist"
        # Index now stores lists of entries (supporting collisions)
        assert len(index[compound_key]) >= 1, "Index should have at least one entry"
        assert index[compound_key][0]["cache_key"] == "entry_000001", "Index should point to entry_000001"

    def test_mtime_update_on_reuse(self, quicken_instance, temp_dir):
        """
        Verify that when a cache entry is reused, its mtime is updated.
        This improves cache hit performance for future lookups.
        """
        test_cpp = temp_dir / "test.cpp"
        args = ['/c', '/nologo', '/EHsc']

        # Step 1: Compile V1
        test_cpp.write_text(TEST_CPP_V1)
        returncode = quicken_instance.run(test_cpp, "cl", args, repo_dir=temp_dir)
        assert returncode == 0

        # Get original mtime from metadata
        cache_dir = Path.home() / ".quicken" / "cache"
        entry_001 = cache_dir / "entry_000001"
        metadata_file = entry_001 / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata_v1 = json.load(f)
        original_mtime = metadata_v1["dependencies"][0]["mtime_ns"]

        # Step 2: Touch file (change mtime but not content)
        time.sleep(0.01)
        test_cpp.write_text(TEST_CPP_V1)  # Same content, new mtime

        # Step 3: Compile again - should be cache hit with mtime update
        returncode = quicken_instance.run(test_cpp, "cl", args, repo_dir=temp_dir)
        assert returncode == 0

        # Step 4: Verify mtime was updated in metadata
        with open(metadata_file, 'r') as f:
            metadata_v2 = json.load(f)
        new_mtime = metadata_v2["dependencies"][0]["mtime_ns"]

        assert new_mtime != original_mtime, "mtime should be updated after cache hit"

    def test_different_args_create_separate_entries(self, quicken_instance, temp_dir):
        """
        Verify that different tool arguments create separate cache entries
        even with the same file content.
        """
        test_cpp = temp_dir / "test.cpp"
        test_cpp.write_text(TEST_CPP_V1)

        args1 = ['/c', '/nologo', '/EHsc']
        args2 = ['/c', '/nologo', '/EHsc', '/W4']

        # Compile with args1
        returncode = quicken_instance.run(test_cpp, "cl", args1, repo_dir=temp_dir)
        assert returncode == 0

        # Compile with args2 (different args, same content)
        returncode = quicken_instance.run(test_cpp, "cl", args2, repo_dir=temp_dir)
        assert returncode == 0

        # Verify 2 entries were created (not reused)
        cache_dir = Path.home() / ".quicken" / "cache"
        cache_entries = [d for d in cache_dir.iterdir() if d.is_dir() and d.name.startswith("entry_")]
        assert len(cache_entries) == 2, "Different args should create separate cache entries"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
