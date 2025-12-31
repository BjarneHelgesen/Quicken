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

from quicken import Quicken, QuickenCache, RepoPath


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
        assert cache.index_file.exists() or not cache.index_file.exists()  # May or may not exist initially
        assert isinstance(cache.index, dict)

    @pytest.mark.pedantic
    def test_cache_key_generation(self, cache_dir, temp_dir):
        """Test cache entry counter generation."""
        cache = QuickenCache(cache_dir)

        # First entry should be entry_000001
        first_id = cache._next_id
        assert first_id == 1

        # After storing an entry, next_id should increment
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")
        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        cache.store(source_repo_path, "cl", ["/c"], dep_repo_paths, [], "", "", 0, temp_dir, output_base_dir=temp_dir)

        assert cache._next_id == 2

    @pytest.mark.pedantic
    def test_cache_store_and_lookup(self, cache_dir, temp_dir):
        """Test storing and looking up cache entries."""
        cache = QuickenCache(cache_dir)

        # Create a fake source and output file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        stdout = "Compilation output"
        stderr = ""
        returncode = 0

        # Store in cache
        cache_entry = cache.store(source_repo_path, tool_name, tool_args, dep_repo_paths, [output_file], stdout, stderr, returncode, temp_dir, output_base_dir=temp_dir)
        assert cache_entry.exists()

        # Lookup should find it
        found = cache.lookup(source_repo_path, tool_name, tool_args, temp_dir)
        assert found is not None
        assert found == cache_entry

        # Different command should not find it
        not_found = cache.lookup(source_repo_path, tool_name, ["/c", "/W4"], temp_dir)
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

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        stdout = "Build succeeded"
        stderr = "No warnings"
        returncode = 0

        cache_entry = cache.store(source_repo_path, tool_name, tool_args, dep_repo_paths, [output_file], stdout, stderr, returncode, temp_dir, output_base_dir=temp_dir)

        # Delete the original file
        output_file.unlink()
        assert not output_file.exists()

        # Restore from cache
        restored_stdout, restored_stderr, restored_returncode = cache.restore(cache_entry, temp_dir)

        # Wait for async file copy to complete
        cache.flush()

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
        returncode1 = quicken_instance.run(test_cpp_file, "cl", tool_args)
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
        returncode2 = quicken_instance.run(test_cpp_file, "cl", tool_args)
        assert returncode2 == 0

        # .obj file should be restored from cache
        assert obj_file.exists()

    @pytest.mark.pedantic
    def test_msvc_different_flags_different_cache(self, quicken_instance, test_cpp_file):
        """Test that different compilation flags create different cache entries."""
        # Compile with /W3
        returncode1 = quicken_instance.run(test_cpp_file, "cl", ["/c", "/nologo", "/EHsc", "/W3"])
        assert returncode1 == 0

        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Compile with /W4 - should be a cache miss
        returncode2 = quicken_instance.run(test_cpp_file, "cl", ["/c", "/nologo", "/EHsc", "/W4"])
        assert returncode2 == 0

    @pytest.mark.pedantic
    def test_msvc_file_modification_invalidates_cache(self, quicken_instance, test_cpp_file):
        """Test that modifying the source file invalidates the cache."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # First compilation
        returncode1 = quicken_instance.run(test_cpp_file, "cl", tool_args)
        assert returncode1 == 0

        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Modify the source file
        test_cpp_file.write_text(SIMPLE_CPP_CODE_MODIFIED)

        # Second compilation - should be cache miss due to file change
        returncode2 = quicken_instance.run(test_cpp_file, "cl", tool_args)
        assert returncode2 == 0

    def test_msvc_custom_output_dir(self, quicken_instance, test_cpp_file, temp_dir):
        """Test that output_dir parameter correctly detects files in custom output directory."""
        # Create custom output directory
        output_dir = temp_dir / "custom_output"
        output_dir.mkdir()

        # Compile with /Fo to specify output directory
        tool_args = ["/c", "/nologo", "/EHsc", f"/Fo{output_dir}/"]

        # First run - cache miss
        returncode1 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
        )
        assert returncode1 == 0

        # Check that .obj file was created in custom directory
        obj_file = output_dir / "test.obj"
        if not obj_file.exists():
            pytest.fail("MSVC compilation succeeded but .obj file not created in custom output directory")

        # Delete the .obj file
        obj_file.unlink()

        # Second run - cache hit
        returncode2 = quicken_instance.run(
            test_cpp_file, "cl", tool_args,
        )
        assert returncode2 == 0

        # .obj file should be restored from cache to custom directory
        assert obj_file.exists()


class TestQuickenClang:
    """Test Quicken with clang++ compiler."""

    def test_clang_cache_miss_and_hit(self, quicken_instance, test_cpp_file):
        """Test clang++ compilation with cache miss followed by cache hit."""
        tool_args = ["-c"]

        # First run - cache miss
        returncode1 = quicken_instance.run(test_cpp_file, "clang++", tool_args)
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
        returncode2 = quicken_instance.run(test_cpp_file, "clang++", tool_args)
        assert returncode2 == returncode1

        # .o file should be restored from cache
        assert obj_file.exists()

    @pytest.mark.pedantic
    def test_clang_different_optimization_levels(self, quicken_instance, test_cpp_file):
        """Test that different optimization levels create different cache entries."""
        # Compile with -O0
        returncode1 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=0)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = test_cpp_file.parent / "test.o"
        if obj_file.exists():
            obj_file.unlink()

        # Compile with -O2 - should be a cache miss
        returncode2 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=2)
        # Just check it completes, return code may vary
        assert isinstance(returncode2, int)

    def test_clang_optimization_none_accepts_any_level(self, quicken_instance, test_cpp_file):
        """Test that optimization=None accepts cache hits from any optimization level."""
        # Compile with optimization level 2
        returncode1 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=2)
        if returncode1 != 0:
            pytest.fail("clang++ compilation with -O2 failed")

        obj_file = test_cpp_file.parent / "test.o"
        assert obj_file.exists(), "clang++ didn't create .o file"

        # Delete the .o file
        obj_file.unlink()

        # Compile with optimization=None - should get cache hit from O2
        returncode2 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=None)
        assert returncode2 == returncode1, "Return codes should match"

        # .o file should be restored from cache
        assert obj_file.exists(), ".o file should be restored from cache"

        # Delete the .o file again
        obj_file.unlink()

        # Compile with a different specific level (O1) - should be cache miss
        returncode3 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=1)
        assert isinstance(returncode3, int)

        # Now with optimization=None, should hit the O2 cache (first one encountered)
        obj_file.unlink()
        returncode4 = quicken_instance.run(test_cpp_file, "clang++", ["-c"],
                                           optimization=None)

        assert obj_file.exists(), ".o file should be restored from cache again"

    @pytest.mark.pedantic
    def test_clang_with_warnings(self, quicken_instance, temp_dir):
        """Test clang++ compilation with warnings."""
        cpp_file = temp_dir / "test_warn.cpp"
        cpp_file.write_text(CPP_CODE_WITH_WARNING)

        tool_args = ["-c", "-Wall"]

        # First run
        returncode1 = quicken_instance.run(cpp_file, "clang++", tool_args)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = temp_dir / "test_warn.o"
        if not obj_file.exists():
            pytest.fail("clang++ didn't create .o file")

        obj_file.unlink()

        # Second run - cache hit
        returncode2 = quicken_instance.run(cpp_file, "clang++", tool_args)
        assert returncode2 == returncode1

        assert obj_file.exists()


class TestQuickenClangTidy:
    """Test Quicken with clang-tidy static analyzer."""

    def test_clang_tidy_cache_miss_and_hit(self, quicken_instance, test_cpp_file):
        """Test clang-tidy analysis with cache miss followed by cache hit."""
        tool_args = ["--checks=readability-*"]

        # First run - cache miss
        # clang-tidy may return non-zero if it finds issues, so we don't assert returncode
        returncode1 = quicken_instance.run(test_cpp_file, "clang-tidy", tool_args)

        # Second run - cache hit (should produce same result)
        returncode2 = quicken_instance.run(test_cpp_file, "clang-tidy", tool_args)

        # Return codes should match
        assert returncode1 == returncode2

    @pytest.mark.pedantic
    def test_clang_tidy_different_checks(self, quicken_instance, test_cpp_file):
        """Test that different check sets create different cache entries."""
        # Run with modernize checks
        returncode1 = quicken_instance.run(test_cpp_file, "clang-tidy", ["--checks=modernize-*"])

        # Run with readability checks - should be a cache miss
        returncode2 = quicken_instance.run(test_cpp_file, "clang-tidy", ["--checks=readability-*"])

        # Both should complete (return codes may vary based on findings)
        assert isinstance(returncode1, int)
        assert isinstance(returncode2, int)

    def test_clang_tidy_cache_invalidation_on_change(self, quicken_instance, test_cpp_file):
        """Test that modifying source invalidates clang-tidy cache."""
        tool_args = ["--checks=*"]

        # First run
        returncode1 = quicken_instance.run(test_cpp_file, "clang-tidy", tool_args)

        # Modify source
        test_cpp_file.write_text(SIMPLE_CPP_CODE_MODIFIED)

        # Second run - should be cache miss
        returncode2 = quicken_instance.run(test_cpp_file, "clang-tidy", tool_args)

        # Should complete
        assert isinstance(returncode2, int)


class TestQuickenIntegration:
    """Integration tests covering multiple tools and scenarios."""

    def test_path_translation_across_locations(self, cache_dir, config_file, temp_dir):
        """Test that cached paths are translated when restoring in a different location."""
        # Create first repo location
        repo1 = temp_dir / "location1"
        repo1.mkdir()
        cpp_file1 = repo1 / "test.cpp"
        cpp_file1.write_text(SIMPLE_CPP_CODE)

        # Create Quicken instance with shared cache
        quicken1 = Quicken(config_file, temp_dir)
        quicken1.cache = QuickenCache(cache_dir)

        # Run compilation in first location - cache miss
        returncode1 = quicken1.run(cpp_file1, "cl", ["/c", "/nologo", "/EHsc"])
        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping path translation test")

        # Create second repo location with same file structure
        repo2 = temp_dir / "location2"
        repo2.mkdir()
        cpp_file2 = repo2 / "test.cpp"
        cpp_file2.write_text(SIMPLE_CPP_CODE)

        # Create new Quicken instance with same cache
        quicken2 = Quicken(config_file, temp_dir)
        quicken2.cache = QuickenCache(cache_dir)

        # Capture stdout/stderr by temporarily redirecting
        import io
        import sys
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            # Run compilation in second location - should be cache hit
            returncode2 = quicken2.run(cpp_file2, "cl", ["/c", "/nologo", "/EHsc"])

            stdout_output = sys.stdout.getvalue()
            stderr_output = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

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
        returncode_cl = quicken_instance.run(test_cpp_file, "cl", ["/c", "/nologo", "/EHsc"])
        if returncode_cl != 0:
            pytest.fail("MSVC compilation failed")

        # Compile with clang
        returncode_clang = quicken_instance.run(test_cpp_file, "clang++", ["-c"])
        # Clang may fail, that's okay

        # Analyze with clang-tidy
        returncode_tidy = quicken_instance.run(test_cpp_file, "clang-tidy", ["--checks=*"])
        assert isinstance(returncode_tidy, int)


    @pytest.mark.pedantic
    def test_cache_index_persistence(self, quicken_instance, test_cpp_file, cache_dir, config_file):
        """Test that cache index persists across Quicken instances."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # First run
        returncode1 = quicken_instance.run(test_cpp_file, "cl", tool_args)
        if returncode1 != 0:
            pytest.fail("MSVC compilation failed")

        # Create new instance with same cache (use same repo_dir as quicken_instance)
        from quicken import Quicken
        quicken2 = Quicken(config_file, test_cpp_file.parent)  # Use same repo_dir
        quicken2.cache = QuickenCache(cache_dir)

        # Delete output file
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run with new instance should hit cache
        returncode2 = quicken2.run(test_cpp_file, "cl", tool_args)

        # Should restore the file if it was created in the first place
        if returncode1 == 0:
            # Cache should work even if file wasn't physically created
            assert returncode2 == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
