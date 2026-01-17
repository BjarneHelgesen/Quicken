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
def quicken_instance(temp_dir):
    """Create a Quicken instance with clean cache."""
    q = Quicken(temp_dir)
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
        cl = quicken_instance.cl(args, [], [])

        # Step 1: Compile V1
        test_cpp.write_text(TEST_CPP_V1)
        _, _, returncode = quicken_instance.run(test_cpp, cl)
        assert returncode == 0

        # Find the compound folder for test.cpp
        cache_dir = Path.home() / ".quicken" / "cache"
        compound_folders = [d for d in cache_dir.iterdir() if d.is_dir() and "test.cpp" in d.name]
        assert len(compound_folders) == 1, f"Should have exactly one compound folder for test.cpp, found {len(compound_folders)}"
        compound_folder = compound_folders[0]

        # Verify entry_000001 was created
        entry_001 = compound_folder / "entry_000001"
        assert entry_001.exists(), "entry_000001 should exist after first compilation"

        # Step 2: Modify and compile V2
        time.sleep(0.01)  # Ensure different mtime
        test_cpp.write_text(TEST_CPP_V2)
        _, _, returncode = quicken_instance.run(test_cpp, cl)
        assert returncode == 0

        # Verify entry_000002 was created
        entry_002 = compound_folder / "entry_000002"
        assert entry_002.exists(), "entry_000002 should exist after second compilation"

        # Step 3: Revert to V1 and compile
        time.sleep(0.01)  # Ensure different mtime
        test_cpp.write_text(TEST_CPP_V1)
        _, _, returncode = quicken_instance.run(test_cpp, cl)
        assert returncode == 0

        # Step 4: Verify entry_000003 does NOT exist (entry_000001 was reused)
        entry_003 = compound_folder / "entry_000003"
        assert not entry_003.exists(), "entry_000003 should NOT exist - entry_000001 should be reused"

        # Verify only 2 entries exist in the compound folder
        cache_entries = [d for d in compound_folder.iterdir() if d.is_dir() and d.name.startswith("entry_")]
        assert len(cache_entries) == 2, f"Expected 2 cache entries, found {len(cache_entries)}"

    def test_mtime_update_on_reuse(self, quicken_instance, temp_dir):
        """
        Verify that when a cache entry is reused, its mtime is updated.
        This improves cache hit performance for future lookups.
        """
        test_cpp = temp_dir / "test.cpp"
        args = ['/c', '/nologo', '/EHsc']
        cl = quicken_instance.cl(args, [], [])

        # Step 1: Compile V1
        test_cpp.write_text(TEST_CPP_V1)
        _, _, returncode = quicken_instance.run(test_cpp, cl)
        assert returncode == 0

        # Find the compound folder for test.cpp
        cache_dir = Path.home() / ".quicken" / "cache"
        compound_folders = [d for d in cache_dir.iterdir() if d.is_dir() and "test.cpp" in d.name]
        assert len(compound_folders) == 1, f"Should have exactly one compound folder for test.cpp, found {len(compound_folders)}"
        compound_folder = compound_folders[0]

        # Get original mtime from metadata
        entry_001 = compound_folder / "entry_000001"
        metadata_file = entry_001 / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata_v1 = json.load(f)
        original_mtime = metadata_v1["dependencies"][0]["mtime_ns"]

        # Step 2: Touch file (change mtime but not content)
        time.sleep(0.01)
        test_cpp.write_text(TEST_CPP_V1)  # Same content, new mtime

        # Step 3: Compile again - should be cache hit with mtime update
        _, _, returncode = quicken_instance.run(test_cpp, cl)
        assert returncode == 0

        # Step 4: Verify mtime was updated in metadata
        with open(metadata_file, 'r') as f:
            metadata_v2 = json.load(f)
        new_mtime = metadata_v2["dependencies"][0]["mtime_ns"]

        assert new_mtime != original_mtime, "mtime should be updated after cache hit"

    def test_different_args_create_separate_entries(self, quicken_instance, temp_dir):
        """
        Verify that different tool arguments create separate compound folders
        even with the same file content.
        """
        test_cpp = temp_dir / "test.cpp"
        test_cpp.write_text(TEST_CPP_V1)

        args1 = ['/c', '/nologo', '/EHsc']
        args2 = ['/c', '/nologo', '/EHsc', '/W4']
        cl1 = quicken_instance.cl(args1, [], [])
        cl2 = quicken_instance.cl(args2, [], [])

        # Compile with args1
        _, _, returncode = quicken_instance.run(test_cpp, cl1)
        assert returncode == 0

        # Compile with args2 (different args, same content)
        _, _, returncode = quicken_instance.run(test_cpp, cl2)
        assert returncode == 0

        # Verify 2 compound folders were created (different args = different folders)
        cache_dir = Path.home() / ".quicken" / "cache"
        compound_folders = [d for d in cache_dir.iterdir() if d.is_dir() and "test.cpp" in d.name]
        assert len(compound_folders) == 2, f"Different args should create separate compound folders, found {len(compound_folders)}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
