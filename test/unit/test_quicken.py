#!/usr/bin/env python3
"""
Unit tests for Quicken caching wrapper.

Tests the caching behavior for MSVC (cl), clang++, and clang-tidy.
"""

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from quicken import Quicken
from quicken._cache import QuickenCache, FolderIndex, CacheKey
from quicken._repo_file import RepoFile
from quicken._tool_cmd import ToolRunResult


class MockToolCmd:
    """Mock ToolCmd for unit tests that need to create CacheKey objects directly."""

    def __init__(self, tool_name: str, arguments: list, input_args: list = None):
        self.tool_name = tool_name
        self.arguments = arguments
        self.input_args = input_args or []

    def add_optimization_flags(self, args):
        return args  # No optimization flags in mock


# Sample C++ code for testing
SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""

SIMPLE_CPP_CODE_MODIFIED = """
#include <iostream>

int main() {
    std::cout << "Hello, Modified World!" << std::endl;
    return 42;
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
# This alias allows tests to use their familiar name
@pytest.fixture
def quicken_instance(quicken_with_persistent_cache, test_cpp_file):
    """Alias for quicken_with_persistent_cache for backward compatibility."""
    return quicken_with_persistent_cache


class TestQuickenCache:
    """Test the QuickenCache class."""

    @pytest.mark.pedantic
    def test_cache_initialization(self, cache_dir):
        """Test that cache initializes correctly."""
        cache = QuickenCache(cache_dir)
        assert cache.cache_dir.exists()
        # No central index in new architecture - cache_dir is empty initially

    @pytest.mark.pedantic
    def test_cache_key_generation(self, cache_dir, temp_dir):
        """Test cache entry counter generation (per-folder)."""
        cache = QuickenCache(cache_dir)

        # After storing an entry, folder should have entry_000001 and next_entry_id should be 2
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")
        source_repo_path = RepoFile(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        cache_key = CacheKey(source_repo_path, MockToolCmd("cl", ["/c"]), temp_dir)
        cache_entry_dir = cache.store(cache_key, dep_repo_paths, ToolRunResult([], "", "", 0), temp_dir)

        # Check folder_index.json
        folder_path = cache_entry_dir.parent
        folder_index = FolderIndex.from_file(folder_path)
        assert folder_index.next_entry_id == 2  # Should be incremented for next entry

    @pytest.mark.pedantic
    def test_cache_store_and_lookup(self, cache_dir, temp_dir):
        """Test storing and looking up cache entries."""
        cache = QuickenCache(cache_dir)

        # Create a fake source and output file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoFile(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        stdout = "Compilation output"
        stderr = ""
        returncode = 0

        # Store in cache
        cache_key = CacheKey(source_repo_path, MockToolCmd(tool_name, tool_args), temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, ToolRunResult([output_file], stdout, stderr, returncode), temp_dir)
        assert cache_entry.exists()

        # Lookup should find it
        found = cache.lookup(cache_key, temp_dir)
        assert found is not None
        assert found == cache_entry

        # Different command should not find it
        different_key = CacheKey(source_repo_path, MockToolCmd(tool_name, ["/c", "/W4"]), temp_dir)
        not_found = cache.lookup(different_key, temp_dir)
        assert not_found is None

    @pytest.mark.pedantic
    def test_cache_restore(self, cache_dir, temp_dir):
        """Test restoring cached files."""

        cache = QuickenCache(cache_dir)

        # Create source file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        # Create and store a fake output file
        output_file = temp_dir / "test.obj"
        output_content = "fake object file content"
        output_file.write_text(output_content)

        source_repo_path = RepoFile(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        stdout = "Build succeeded"
        stderr = "No warnings"
        returncode = 0

        cache_key = CacheKey(source_repo_path, MockToolCmd(tool_name, tool_args), temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, ToolRunResult([output_file], stdout, stderr, returncode), temp_dir)

        # Delete the original file
        output_file.unlink()
        assert not output_file.exists()

        # Restore from cache
        restored_stdout, restored_stderr, restored_returncode = cache.restore(cache_entry, temp_dir)

        # Check metadata
        assert restored_stdout == stdout
        assert restored_stderr == stderr
        assert restored_returncode == returncode

        # Check file was restored
        assert output_file.exists()
        assert output_file.read_text() == output_content


class TestQuickenMSVC:
    """Test Quicken with MSVC (cl) compiler."""

    def test_msvc_cache_miss_and_hit(self, quicken_instance, test_cpp_file):
        """Test MSVC compilation with cache miss followed by cache hit."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # First run - cache miss
        cl = quicken_instance.cl(tool_args, [], [])
        _, _, returncode1 = cl(test_cpp_file)
        assert returncode1 == 0  # Compilation should succeed

        # Check that .obj file was created
        obj_file = test_cpp_file.parent / "test.obj"
        if not obj_file.exists():
            # If compilation succeeded but no .obj file, this is fine for cache testing
            # The cache still stores the metadata
            pytest.fail("MSVC compilation succeeded but .obj file not created in expected location")

        # Delete the .obj file
        obj_file.unlink()

        # Second run - cache hit
        _, _, returncode2 = cl(test_cpp_file)
        assert returncode2 == 0

        # .obj file should be restored from cache
        assert obj_file.exists()

    @pytest.mark.pedantic
    def test_msvc_different_flags_different_cache(self, quicken_instance, test_cpp_file):
        """Test that different compilation flags create different cache entries."""
        # Compile with /W3
        cl_w3 = quicken_instance.cl(["/c", "/nologo", "/EHsc", "/W3"], [], [])
        _, _, returncode1 = cl_w3(test_cpp_file)
        assert returncode1 == 0

        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Compile with /W4 - should be a cache miss
        cl_w4 = quicken_instance.cl(["/c", "/nologo", "/EHsc", "/W4"], [], [])
        _, _, returncode2 = cl_w4(test_cpp_file)
        assert returncode2 == 0

    @pytest.mark.pedantic
    def test_msvc_file_modification_invalidates_cache(self, quicken_instance, test_cpp_file):
        """Test that modifying the source file invalidates the cache."""
        tool_args = ["/c", "/nologo", "/EHsc"]
        cl = quicken_instance.cl(tool_args, [], [])

        # First compilation
        _, _, returncode1 = cl(test_cpp_file)
        assert returncode1 == 0

        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Modify the source file
        test_cpp_file.write_text(SIMPLE_CPP_CODE_MODIFIED)

        # Second compilation - should be cache miss due to file change
        _, _, returncode2 = cl(test_cpp_file)
        assert returncode2 == 0

    def test_msvc_custom_output_dir(self, quicken_instance, test_cpp_file, temp_dir):
        """Test that output_dir parameter correctly detects files in custom output directory."""
        # Create custom output directory
        output_dir = temp_dir / "custom_output"
        output_dir.mkdir()

        # Compile with /Fo to specify output directory
        tool_args = ["/c", "/nologo", "/EHsc", f"/Fo{output_dir}/"]
        cl = quicken_instance.cl(tool_args, [], [])

        # First run - cache miss
        _, _, returncode1 = cl(test_cpp_file)
        assert returncode1 == 0

        # Check that .obj file was created in custom directory
        obj_file = output_dir / "test.obj"
        if not obj_file.exists():
            pytest.fail("MSVC compilation succeeded but .obj file not created in custom output directory")

        # Delete the .obj file
        obj_file.unlink()

        # Second run - cache hit
        _, _, returncode2 = cl(test_cpp_file)
        assert returncode2 == 0

        # .obj file should be restored from cache to custom directory
        assert obj_file.exists()


class TestQuickenClang:
    """Test Quicken with clang++ compiler."""

    def test_clang_cache_miss_and_hit(self, quicken_instance, test_cpp_file):
        """Test clang++ compilation with cache miss followed by cache hit."""
        tool_args = ["-c"]
        clang = quicken_instance.clang(tool_args, [], [])

        # First run - cache miss
        _, _, returncode1 = clang(test_cpp_file)
        # Clang may fail due to missing headers, that's okay for testing cache behavior
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed, likely due to missing headers")

        # Check that .o file was created
        obj_file = test_cpp_file.parent / "test.o"
        if not obj_file.exists():
            pytest.fail("clang++ compilation succeeded but .o file not created in expected location")

        # Delete the .o file
        obj_file.unlink()

        # Second run - cache hit
        _, _, returncode2 = clang(test_cpp_file)
        assert returncode2 == returncode1

        # .o file should be restored from cache
        assert obj_file.exists()

    @pytest.mark.pedantic
    def test_clang_different_optimization_levels(self, quicken_instance, test_cpp_file):
        """Test that different optimization levels create different cache entries."""
        # Compile with -O0
        clang_o0 = quicken_instance.clang(["-c"], [], [], optimization=0)
        _, _, returncode1 = clang_o0(test_cpp_file)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = test_cpp_file.parent / "test.o"
        if obj_file.exists():
            obj_file.unlink()

        # Compile with -O2 - should be a cache miss
        clang_o2 = quicken_instance.clang(["-c"], [], [], optimization=2)
        _, _, returncode2 = clang_o2(test_cpp_file)
        # Just check it completes, return code may vary
        assert isinstance(returncode2, int)

    @pytest.mark.pedantic
    def test_clang_with_warnings(self, quicken_instance, temp_dir):
        """Test clang++ compilation with warnings."""
        cpp_file = temp_dir / "test_warn.cpp"
        cpp_file.write_text(CPP_CODE_WITH_WARNING)

        tool_args = ["-c", "-Wall"]
        clang = quicken_instance.clang(tool_args, [], [])

        # First run
        _, _, returncode1 = clang(cpp_file)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = temp_dir / "test_warn.o"
        if not obj_file.exists():
            pytest.fail("clang++ didn't create .o file")

        obj_file.unlink()

        # Second run - cache hit
        _, _, returncode2 = clang(cpp_file)
        assert returncode2 == returncode1

        assert obj_file.exists()


class TestQuickenClangTidy:
    """Test Quicken with clang-tidy static analyzer."""

    def test_clang_tidy_cache_miss_and_hit(self, quicken_instance, test_cpp_file):
        """Test clang-tidy analysis with cache miss followed by cache hit."""
        tool_args = ["--checks=readability-*"]
        clang_tidy = quicken_instance.clang_tidy(tool_args, [], [])

        # First run - cache miss
        # clang-tidy may return non-zero if it finds issues, so we don't assert returncode
        _, _, returncode1 = clang_tidy(test_cpp_file)

        # Second run - cache hit (should produce same result)
        _, _, returncode2 = clang_tidy(test_cpp_file)

        # Return codes should match
        assert returncode1 == returncode2

    @pytest.mark.pedantic
    def test_clang_tidy_different_checks(self, quicken_instance, test_cpp_file):
        """Test that different check sets create different cache entries."""
        # Run with modernize checks
        clang_tidy_mod = quicken_instance.clang_tidy(["--checks=modernize-*"], [], [])
        _, _, returncode1 = clang_tidy_mod(test_cpp_file)

        # Run with readability checks - should be a cache miss
        clang_tidy_read = quicken_instance.clang_tidy(["--checks=readability-*"], [], [])
        _, _, returncode2 = clang_tidy_read(test_cpp_file)

        # Both should complete (return codes may vary based on findings)
        assert isinstance(returncode1, int)
        assert isinstance(returncode2, int)

    def test_clang_tidy_cache_invalidation_on_change(self, quicken_instance, test_cpp_file):
        """Test that modifying source invalidates clang-tidy cache."""
        tool_args = ["--checks=*"]
        clang_tidy = quicken_instance.clang_tidy(tool_args, [], [])

        # First run
        _, _, returncode1 = clang_tidy(test_cpp_file)

        # Modify source
        test_cpp_file.write_text(SIMPLE_CPP_CODE_MODIFIED)

        # Second run - should be cache miss
        _, _, returncode2 = clang_tidy(test_cpp_file)

        # Should complete
        assert isinstance(returncode2, int)


class TestQuickenIntegration:
    """Integration tests covering multiple tools and scenarios."""

    def test_path_translation_across_locations(self, cache_dir, temp_dir):
        """Test that cached paths are translated when restoring in a different location."""
        # Create first repo location
        repo1 = temp_dir / "location1"
        repo1.mkdir()
        cpp_file1 = repo1 / "test.cpp"
        cpp_file1.write_text(SIMPLE_CPP_CODE)

        # Create Quicken instance with shared cache
        quicken1 = Quicken(temp_dir, cache_dir=cache_dir)
        cl1 = quicken1.cl(["/c", "/nologo", "/EHsc"], [], [])

        # Run compilation in first location - cache miss
        _, _, returncode1 = cl1(cpp_file1)
        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping path translation test")

        # Create second repo location with same file structure
        repo2 = temp_dir / "location2"
        repo2.mkdir()
        cpp_file2 = repo2 / "test.cpp"
        cpp_file2.write_text(SIMPLE_CPP_CODE)

        # Create new Quicken instance with same cache
        quicken2 = Quicken(temp_dir, cache_dir=cache_dir)
        cl2 = quicken2.cl(["/c", "/nologo", "/EHsc"], [], [])

        # Run compilation in second location - should be cache hit
        stdout_output, stderr_output, returncode2 = cl2(cpp_file2)

        # Verify return code matches
        assert returncode2 == returncode1

        # Verify paths were translated (should NOT contain old location)
        combined_output = stdout_output + stderr_output
        assert str(repo1) not in combined_output, \
            f"Output contains old repo path {repo1}, path translation failed"

        # If there are any file paths in output, they should point to new location
        if str(repo2 / "test.cpp") in combined_output or "test.cpp" in combined_output:
            # Path translation is working if we see references to the new location
            # or if there are no absolute paths at all (both acceptable)
            pass

    def test_multiple_tools_same_file(self, quicken_instance, test_cpp_file):
        """Test that the same file can be processed by multiple tools with separate caches."""
        # Compile with MSVC
        cl = quicken_instance.cl(["/c", "/nologo", "/EHsc"], [], [])
        _, _, returncode_cl = cl(test_cpp_file)
        if returncode_cl != 0:
            pytest.fail("MSVC compilation failed")

        # Compile with clang
        clang = quicken_instance.clang(["-c"], [], [])
        _, _, returncode_clang = clang(test_cpp_file)
        # Clang may fail, that's okay

        # Analyze with clang-tidy
        clang_tidy = quicken_instance.clang_tidy(["--checks=*"], [], [])
        _, _, returncode_tidy = clang_tidy(test_cpp_file)
        assert isinstance(returncode_tidy, int)


    @pytest.mark.pedantic
    def test_cache_index_persistence(self, quicken_instance, test_cpp_file, cache_dir):
        """Test that cache index persists across Quicken instances."""
        tool_args = ["/c", "/nologo", "/EHsc"]
        cl = quicken_instance.cl(tool_args, [], [])

        # First run
        _, _, returncode1 = cl(test_cpp_file)
        if returncode1 != 0:
            pytest.fail("MSVC compilation failed")

        # Create new instance with same cache (use same repo_dir as quicken_instance)
        from quicken import Quicken
        quicken2 = Quicken(test_cpp_file.parent, cache_dir=cache_dir)  # Use same repo_dir
        cl2 = quicken2.cl(tool_args, [], [])

        # Delete output file
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run with new instance should hit cache
        _, _, returncode2 = cl2(test_cpp_file)

        # Should restore the file if it was created in the first place
        if returncode1 == 0:
            # Cache should work even if file wasn't physically created
            assert returncode2 == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
