#!/usr/bin/env python3
"""
Unit test for handling multiple compiles of the same file without clearing artifacts.

Tests that Quicken correctly identifies and caches new artifacts even when they
overwrite existing files from previous compilations.
"""

import json
import tempfile
import time
from pathlib import Path

import pytest

from quicken import Quicken
from quicken._cache import QuickenCache


# Simple C++ code for testing
INITIAL_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Version 1" << std::endl;
    return 0;
}
"""

MODIFIED_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Version 2" << std::endl;
    return 42;
}
"""


@pytest.fixture
def cache_dir(temp_dir):
    """Create a temporary cache directory."""
    cache = temp_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


@pytest.fixture
def quicken_instance(cache_dir, temp_dir):
    """Create a Quicken instance with a custom cache directory."""
    quicken = Quicken(temp_dir, cache_dir=cache_dir)
    return quicken


class TestArtifactOverwrite:
    """Test artifact collection when compiling the same file multiple times."""

    def test_multiple_compiles_without_clearing_artifacts(self, quicken_instance, temp_dir):
        """
        Test that Quicken correctly handles multiple compilations of the same file
        without clearing artifacts between runs.

        Scenario:
        1. Compile my_file.cpp -> produces my_file.obj (cache miss)
        2. Modify my_file.cpp and compile again -> overwrites my_file.obj (cache miss)
        3. Delete the new my_file.obj and compile again -> should restore new version (cache hit)

        The test verifies that:
        - The new my_file.obj from step 2 is collected despite old one existing
        - When restoring from cache, we get the new version, not the old one
        - Timestamp-based artifact detection correctly identifies the new output
        """
        cpp_file = temp_dir / "my_file.cpp"
        obj_file = temp_dir / "my_file.obj"
        tool_args = ["/c", "/nologo", "/EHsc"]

        # Step 1: Initial compilation (cache miss)
        cpp_file.write_text(INITIAL_CPP_CODE)

        returncode1 = quicken_instance.run(
            cpp_file, "cl", tool_args,
        )
        assert returncode1 == 0, "Initial compilation should succeed"
        assert obj_file.exists(), "Initial .obj file should be created"

        # Record the initial obj file's content and timestamp
        initial_obj_content = obj_file.read_bytes()
        initial_obj_mtime = obj_file.stat().st_mtime_ns

        # Small delay to ensure different timestamp
        time.sleep(0.01)

        # Step 2: Modify source and compile again (cache miss, overwrites existing .obj)
        cpp_file.write_text(MODIFIED_CPP_CODE)

        # Verify the old obj file still exists before compilation
        assert obj_file.exists(), "Old .obj file should still exist before recompilation"

        returncode2 = quicken_instance.run(
            cpp_file, "cl", tool_args,
        )
        assert returncode2 == 0, "Modified compilation should succeed"
        assert obj_file.exists(), "New .obj file should exist after recompilation"

        # Record the new obj file's content and timestamp
        new_obj_content = obj_file.read_bytes()
        new_obj_mtime = obj_file.stat().st_mtime_ns

        # Verify the obj file has actually changed
        assert new_obj_content != initial_obj_content, "New .obj should have different content"
        assert new_obj_mtime > initial_obj_mtime, "New .obj should have newer timestamp"

        # Step 3: Delete the new obj file and compile again (should be cache hit)
        obj_file.unlink()
        assert not obj_file.exists(), "New .obj should be deleted"

        returncode3 = quicken_instance.run(
            cpp_file, "cl", tool_args,
        )
        assert returncode3 == 0, "Cache hit compilation should succeed"
        assert obj_file.exists(), "Cached .obj file should be restored"

        # Verify the restored file matches the NEW version, not the old one
        restored_obj_content = obj_file.read_bytes()
        assert restored_obj_content == new_obj_content, \
            "Restored .obj should match NEW version from step 2"
        assert restored_obj_content != initial_obj_content, \
            "Restored .obj should NOT match old version from step 1"

    def test_artifact_detection_with_preexisting_file(self, quicken_instance, temp_dir):
        """
        Test that artifact detection correctly identifies new output even when
        a file with the same name already exists.

        This ensures the artifact collection uses timestamps to distinguish
        between pre-existing files and actual tool outputs.
        """
        cpp_file = temp_dir / "test.cpp"
        obj_file = temp_dir / "test.obj"
        tool_args = ["/c", "/nologo", "/EHsc"]

        # Create initial source
        cpp_file.write_text(INITIAL_CPP_CODE)

        # Create a pre-existing obj file (from previous run, not cleaned up)
        obj_file.write_text("old artifact content")
        old_mtime = obj_file.stat().st_mtime_ns

        # Small delay to ensure different timestamp
        time.sleep(0.01)

        # Compile - should detect and cache the NEW obj file, not the old one
        returncode = quicken_instance.run(
            cpp_file, "cl", tool_args,
        )
        assert returncode == 0, "Compilation should succeed"
        assert obj_file.exists(), "New .obj file should exist"

        # Verify the obj file was updated (new timestamp)
        new_mtime = obj_file.stat().st_mtime_ns
        assert new_mtime > old_mtime, "New .obj should have newer timestamp than pre-existing file"

        # The new obj should be binary content, not our text
        new_content = obj_file.read_bytes()
        assert new_content != b"old artifact content", "New .obj should have compiler-generated content"

        # Delete and restore from cache
        obj_file.unlink()

        returncode2 = quicken_instance.run(
            cpp_file, "cl", tool_args,
        )
        assert returncode2 == 0, "Cache hit should succeed"
        assert obj_file.exists(), "Cached .obj should be restored"

        # Verify restored file matches the NEW content
        restored_content = obj_file.read_bytes()
        assert restored_content == new_content, "Restored content should match NEW artifact"
        assert restored_content != b"old artifact content", "Restored content should not be old artifact"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
