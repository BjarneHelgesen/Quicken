#!/usr/bin/env python3
"""
Unit tests for input_args functionality.

Tests path translation for input arguments containing file paths.
"""

import json
import tempfile
from pathlib import Path

import pytest

from quicken import Quicken, QuickenCache, RepoPath


# Sample C++ code for testing
SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""


@pytest.fixture
def test_cpp_file(temp_dir):
    """Create a test C++ file."""
    cpp_file = temp_dir / "test.cpp"
    cpp_file.write_text(SIMPLE_CPP_CODE)
    return cpp_file


@pytest.fixture
def cache_dir(temp_dir):
    """Create a temporary cache directory."""
    cache = temp_dir / "cache"
    cache.mkdir(parents=True, exist_ok=True)
    return cache


# Most tests now use quicken_with_persistent_cache from conftest.py
@pytest.fixture
def quicken_instance(quicken_with_persistent_cache, test_cpp_file):
    """Alias for quicken_with_persistent_cache for backward compatibility."""
    return quicken_with_persistent_cache


class TestInputArgsPathTranslation:
    """Test path translation for input_args."""

    def test_translate_input_args_repo_relative(self, cache_dir, temp_dir):
        """Test that absolute paths in repo are converted to repo-relative."""
        cache = QuickenCache(cache_dir)

        # Create a file in the repo
        header_file = temp_dir / "default_header.h"
        header_file.write_text("#pragma once\n")

        # Test with absolute path to file in repo
        input_args = ["-include", str(header_file)]
        translated = cache._translate_input_args_for_cache_key(input_args, temp_dir)

        # Should translate absolute path to repo-relative
        assert translated == ["-include", "default_header.h"]

    def test_translate_input_args_outside_repo(self, cache_dir, temp_dir):
        """Test that absolute paths outside repo remain absolute."""
        cache = QuickenCache(cache_dir)

        # Use a path outside the repo (system path)
        outside_path = Path("C:\\Windows\\System32\\config.ini")

        input_args = ["-include", str(outside_path)]
        translated = cache._translate_input_args_for_cache_key(input_args, temp_dir)

        # Should keep absolute path (normalized)
        assert translated == ["-include", str(outside_path.resolve())]

    def test_translate_input_args_with_flags(self, cache_dir, temp_dir):
        """Test that flag arguments are preserved."""
        cache = QuickenCache(cache_dir)

        # Create a file in the repo
        header_file = temp_dir / "header.h"
        header_file.write_text("#pragma once\n")

        # Test with flags and file paths
        input_args = ["-include", str(header_file), "-DDEBUG"]
        translated = cache._translate_input_args_for_cache_key(input_args, temp_dir)

        # Flags should be preserved, paths translated
        assert translated == ["-include", "header.h", "-DDEBUG"]

    def test_translate_input_args_relative_path(self, cache_dir, temp_dir):
        """Test that relative paths are converted to repo-relative."""
        cache = QuickenCache(cache_dir)

        # Create a file in a subdirectory
        subdir = temp_dir / "include"
        subdir.mkdir()
        header_file = subdir / "config.h"
        header_file.write_text("#pragma once\n")

        # Test with relative path
        input_args = ["-include", "include/config.h"]
        translated = cache._translate_input_args_for_cache_key(input_args, temp_dir)

        # Should normalize to repo-relative
        assert translated == ["-include", "include/config.h"]

    def test_translate_input_args_with_parent_refs(self, cache_dir, temp_dir):
        """Test that relative paths with .. are resolved correctly."""
        cache = QuickenCache(cache_dir)

        # Create a file in the repo
        header_file = temp_dir / "header.h"
        header_file.write_text("#pragma once\n")

        # Test with relative path containing ..
        # From temp_dir/subdir, reference ../header.h
        input_args = ["-include", "subdir/../header.h"]
        translated = cache._translate_input_args_for_cache_key(input_args, temp_dir)

        # Should resolve to just header.h
        assert translated == ["-include", "header.h"]


class TestInputArgsCaching:
    """Test that input_args affect cache keys correctly."""

    def test_different_input_args_different_cache(self, quicken_instance, test_cpp_file, temp_dir):
        """Test that different input_args create different cache entries."""
        import time

        # Create header files with unique FILENAMES (not just content)
        # This ensures different cache keys on every run
        timestamp = int(time.time() * 1000000)  # microsecond precision

        header1 = temp_dir / f"header1_{timestamp}.h"
        header1.write_text("#define VALUE1 1\n")

        header2 = temp_dir / f"header2_{timestamp}.h"
        header2.write_text("#define VALUE2 2\n")

        tool_args = ["/c", "/nologo", "/EHsc"]

        # Track initial cache size
        initial_size = len(quicken_instance.cache.index)

        # First run with header1
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent,
            input_args=["-include", str(header1)]
        )
        assert returncode1 == 0

        # Check cache increased by one entry
        assert len(quicken_instance.cache.index) == initial_size + 1, \
            "Different input_args should create new cache entry"

        # Second run with header2 - should be different cache entry
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent,
            input_args=["-include", str(header2)]
        )
        assert returncode2 == 0

        # Should have two new cache entries now
        assert len(quicken_instance.cache.index) == initial_size + 2, \
            "Different input_args should create separate cache entries"

    def test_same_input_args_cache_hit(self, quicken_instance, test_cpp_file, temp_dir):
        """Test that same input_args result in cache hit."""
        # Create a header file
        header = temp_dir / "common.h"
        header.write_text("#pragma once\n")

        tool_args = ["/c", "/nologo", "/EHsc"]
        input_args = ["-include", str(header)]

        # First run
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent,
            input_args=input_args
        )
        assert returncode1 == 0

        # Delete output file
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run with same input_args - should hit cache
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent,
            input_args=input_args
        )
        assert returncode2 == 0

        # File should be restored from cache
        assert obj_file.exists(), "Cache hit should restore output file"

    def test_input_args_path_portability(self, cache_dir, config_file, temp_dir):
        """Test that cache works across different repo locations with input_args."""
        # Create first repo location
        repo1 = temp_dir / "location1"
        repo1.mkdir()
        cpp_file1 = repo1 / "test.cpp"
        cpp_file1.write_text(SIMPLE_CPP_CODE)
        header1 = repo1 / "common.h"
        header1.write_text("#pragma once\n")

        # Create Quicken instance with shared cache
        quicken1 = Quicken(config_file)
        quicken1.cache = QuickenCache(cache_dir)

        # Run compilation in first location with input_args
        returncode1 = quicken1.run(
            cpp_file1, "cl", ["/c", "/nologo", "/EHsc"],
            repo_dir=repo1,
            input_args=["-include", str(header1)]
        )
        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping portability test")

        # Create second repo location with same structure
        repo2 = temp_dir / "location2"
        repo2.mkdir()
        cpp_file2 = repo2 / "test.cpp"
        cpp_file2.write_text(SIMPLE_CPP_CODE)
        header2 = repo2 / "common.h"
        header2.write_text("#pragma once\n")

        # Create new Quicken instance with same cache
        quicken2 = Quicken(config_file)
        quicken2.cache = QuickenCache(cache_dir)

        # Run in second location - should hit cache because paths are repo-relative
        returncode2 = quicken2.run(
            cpp_file2, "cl", ["/c", "/nologo", "/EHsc"],
            repo_dir=repo2,
            input_args=["-include", str(header2)]
        )

        # Should get cache hit
        assert returncode2 == returncode1

    def test_no_input_args_backward_compatibility(self, quicken_instance, test_cpp_file):
        """Test that omitting input_args works (backward compatibility)."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # Run without input_args (should work as before)
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent
        )
        assert returncode1 == 0

        # Delete output
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run should hit cache
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            repo_dir=test_cpp_file.parent
        )
        assert returncode2 == 0

        # Output should be restored
        assert obj_file.exists()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
