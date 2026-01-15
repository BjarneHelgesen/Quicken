#!/usr/bin/env python3
"""
Unit tests for input_args functionality.

Tests path translation for input arguments containing file paths.
"""

import json
import tempfile
from pathlib import Path

import pytest

from quicken import Quicken
from quicken._cache import QuickenCache, FolderIndex
from quicken._repo_path import RepoPath


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

        # Create header files with different names
        header1 = temp_dir / "header1.h"
        header1.write_text("#define VALUE1 1\n")

        header2 = temp_dir / "header2.h"
        header2.write_text("#define VALUE2 2\n")

        tool_args = ["/c", "/nologo", "/EHsc"]
        input_args1 = ["-include", str(header1)]
        input_args2 = ["-include", str(header2)]

        # First run with header1
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            input_args=input_args1
        )
        assert returncode1 == 0

        # Second run with header2 - should be different cache entry
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
            input_args=input_args2
        )
        assert returncode2 == 0

        # Verify that both input_args created separate compound folders
        # Different input_args should create different folder hashes
        cache_dir = quicken_instance.cache.cache_dir
        compound_folders = [d for d in cache_dir.iterdir() if d.is_dir() and "test.cpp" in d.name]

        # Find folders with the specific input_args by checking compound_key
        folders_with_header1 = []
        folders_with_header2 = []
        for cf in compound_folders:
            folder_index = FolderIndex.from_file(cf, temp_dir)
            compound_key = folder_index.compound_key
            if "header1.h" in compound_key:
                folders_with_header1.append(cf)
            if "header2.h" in compound_key:
                folders_with_header2.append(cf)

        # Should have at least one folder for each input_args
        assert len(folders_with_header1) > 0, \
            f"Should have compound folder for header1.h input_args. Found {len(compound_folders)} total test.cpp folders. Folders with header1: {len(folders_with_header1)}, with header2: {len(folders_with_header2)}"
        assert len(folders_with_header2) > 0, \
            f"Should have compound folder for header2.h input_args. Found {len(compound_folders)} total test.cpp folders"

        # They should be different folders
        assert folders_with_header1[0] != folders_with_header2[0], \
            "Different input_args should create different compound folders"

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
            input_args=input_args
        )
        assert returncode2 == 0

        # File should be restored from cache
        assert obj_file.exists(), "Cache hit should restore output file"

    def test_input_args_path_portability(self, cache_dir, temp_dir):
        """Test that cache works across different repo locations with input_args."""
        # Create first repo location
        repo1 = temp_dir / "location1"
        repo1.mkdir()
        cpp_file1 = repo1 / "test.cpp"
        cpp_file1.write_text(SIMPLE_CPP_CODE)
        header1 = repo1 / "common.h"
        header1.write_text("#pragma once\n")

        # Create Quicken instance with shared cache
        quicken1 = Quicken(temp_dir, cache_dir=cache_dir)

        # Run compilation in first location with input_args
        returncode1 = quicken1.run(
            cpp_file1, "cl", ["/c", "/nologo", "/EHsc"],
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
        quicken2 = Quicken(temp_dir, cache_dir=cache_dir)

        # Run in second location - should hit cache because paths are repo-relative
        returncode2 = quicken2.run(
            cpp_file2, "cl", ["/c", "/nologo", "/EHsc"],
            input_args=["-include", str(header2)]
        )

        # Should get cache hit
        assert returncode2 == returncode1

    def test_no_input_args_backward_compatibility(self, quicken_instance, test_cpp_file):
        """Test that omitting input_args works (backward compatibility)."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # Run without input_args (should work as before)
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args)
        assert returncode1 == 0

        # Delete output
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run should hit cache
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args)
        assert returncode2 == 0

        # Output should be restored
        assert obj_file.exists()


class TestMultiElementInputArgs:
    """Test multi-element input_args with flag and path pairs."""

    def test_multi_element_input_args_cache_hit(self, cache_dir, temp_dir):
        """Test that multi-element input_args [flag, path] produce cache hits across different repo_dirs."""

        # Create two separate repos with identical source content
        repo1 = temp_dir / "repo1"
        repo2 = temp_dir / "repo2"
        repo1.mkdir()
        repo2.mkdir()

        # Identical source files
        source_code = """
int add(int a, int b) {
    return a + b;
}
"""
        cpp_file1 = repo1 / "test.cpp"
        cpp_file2 = repo2 / "test.cpp"
        cpp_file1.write_text(source_code)
        cpp_file2.write_text(source_code)

        # Create a header file OUTSIDE both repos (absolute path scenario)
        # This simulates LevelUp.h being in the LevelUp repo while test files are elsewhere
        external_header_dir = temp_dir / "external_headers"
        external_header_dir.mkdir()
        header_file = external_header_dir / "common.h"
        header_file.write_text("#pragma once\n#define COMMON 1\n")

        # Create Quicken instance for repo1
        quicken1 = Quicken(repo1, cache_dir=cache_dir)

        # Compile in repo1 with multi-element input_args
        tool_args = ["-std=c++20", "-Wall", "-S", "-masm=intel"]
        input_args = ["-include", str(header_file)]  # Multi-element: [flag, absolute_path]

        returncode1 = quicken1.run(
            cpp_file1.relative_to(repo1),
            "clang++",
            tool_args,
            input_args=input_args,
            output_args=["-o", str(repo1 / "test.s")],
            optimization=0
        )

        if returncode1 != 0:
            pytest.skip("Clang++ compilation failed, skipping cache test")

        # Get cache statistics before second run
        cache_entries_before = len([d for d in cache_dir.iterdir() if d.is_dir()])

        # Create second Quicken instance for repo2 with same cache
        quicken2 = Quicken(repo2, cache_dir=cache_dir)

        # Compile in repo2 - should HIT cache because:
        # - Same source content
        # - Same tool_args
        # - Same input_args (with normalized absolute path to header_file)
        # - Different repo_dir (should NOT affect cache key)
        returncode2 = quicken2.run(
            cpp_file2.relative_to(repo2),
            "clang++",
            tool_args,
            input_args=input_args,
            output_args=["-o", str(repo2 / "test.s")],
            optimization=0
        )

        assert returncode2 == 0, "Second compilation should succeed"

        # Get cache statistics after second run
        cache_entries_after = len([d for d in cache_dir.iterdir() if d.is_dir()])

        # Verify cache hit: no new entry should be created
        assert cache_entries_before == cache_entries_after, \
            f"Cache HIT expected: entries should remain {cache_entries_before}, but got {cache_entries_after}"

    def test_multiple_input_args_pairs(self, cache_dir, temp_dir):
        """Test multiple flag-path pairs in input_args.

        KNOWN BUG: Quicken incorrectly concatenates multiple pairs like:
        ["-include", "path1", "-include", "path2"]
        into a single malformed path: "path1-includepath2"

        This causes compilation to fail looking for a non-existent file.
        """

        repo = temp_dir / "test_repo"
        repo.mkdir()

        source_code = """
int multiply(int x, int y) {
    return x * y;
}
"""
        cpp_file = repo / "main.cpp"
        cpp_file.write_text(source_code)

        # Create multiple header files
        header1 = temp_dir / "header1.h"
        header2 = temp_dir / "header2.h"
        header1.write_text("#pragma once\n#define VALUE1 10\n")
        header2.write_text("#pragma once\n#define VALUE2 20\n")

        # Create Quicken instance for the repo
        quicken = Quicken(repo, cache_dir=cache_dir)

        # Multiple input_args: [flag1, path1, flag2, path2]
        tool_args = ["-std=c++20", "-Wall", "-S", "-masm=intel"]
        input_args = ["-include", str(header1), "-include", str(header2)]

        returncode1 = quicken.run(
            cpp_file.relative_to(repo),
            "clang++",
            tool_args,
            input_args=input_args,
            output_args=["-o", str(repo / "main.s")],
            optimization=0
        )

        # This SHOULD succeed but currently fails due to Quicken bug
        assert returncode1 == 0, "Compilation should succeed with multiple input_args pairs"

        # Delete output file
        output_file = repo / "main.s"
        if output_file.exists():
            output_file.unlink()

        cache_entries_before = len([d for d in cache_dir.iterdir() if d.is_dir()])

        # Second run with same input_args - should HIT cache
        returncode2 = quicken.run(
            cpp_file.relative_to(repo),
            "clang++",
            tool_args,
            input_args=input_args,
            output_args=["-o", str(repo / "main.s")],
            optimization=0
        )

        assert returncode2 == 0
        cache_entries_after = len([d for d in cache_dir.iterdir() if d.is_dir()])

        # Verify cache hit
        assert cache_entries_before == cache_entries_after, \
            "Multiple input_args pairs should produce cache hit on second run"

        # Output file should be restored from cache
        assert output_file.exists(), "Cache hit should restore output file"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
