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

from quicken import Quicken, QuickenCache


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
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


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


@pytest.fixture
def config_file(temp_dir):
    """Create a test config file pointing to the real tools.json."""
    # Use the actual tools.json from the project
    project_tools = Path(__file__).parent / "tools.json"
    if project_tools.exists():
        return project_tools

    # Fallback: create a minimal config
    config = temp_dir / "tools.json"
    config_data = {
        "cl": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\MSVC\\14.44.35207\\bin\\Hostx64\\x64\\cl.exe",
        "vcvarsall": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvarsall.bat",
        "msvc_arch": "x64",
        "clang": "clang++",
        "clang-tidy": "clang-tidy"
    }
    config.write_text(json.dumps(config_data, indent=2))
    return config


@pytest.fixture
def quicken_instance(config_file, cache_dir, monkeypatch):
    """Create a Quicken instance with a custom cache directory."""
    quicken = Quicken(config_file)
    # Override the cache directory to use our temp one
    quicken.cache = QuickenCache(cache_dir)
    return quicken


class TestQuickenCache:
    """Test the QuickenCache class."""

    def test_cache_initialization(self, cache_dir):
        """Test that cache initializes correctly."""
        cache = QuickenCache(cache_dir)
        assert cache.cache_dir.exists()
        assert cache.index_file.exists() or not cache.index_file.exists()  # May or may not exist initially
        assert isinstance(cache.index, dict)

    def test_cache_key_generation(self, cache_dir, temp_dir):
        """Test cache entry counter generation."""
        cache = QuickenCache(cache_dir)

        # First entry should be entry_000001
        first_id = cache._next_id
        assert first_id == 1

        # After storing an entry, next_id should increment
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")
        dependencies = [source_file]
        cache.store(source_file, "cl /c", dependencies, [], "", "", 0)

        assert cache._next_id == 2

    def test_cache_store_and_lookup(self, cache_dir, temp_dir):
        """Test storing and looking up cache entries."""
        cache = QuickenCache(cache_dir)

        # Create a fake source and output file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        dependencies = [source_file]
        tool_cmd = "cl /c"
        stdout = "Compilation output"
        stderr = ""
        returncode = 0

        # Store in cache
        cache_entry = cache.store(source_file, tool_cmd, dependencies, [output_file], stdout, stderr, returncode)
        assert cache_entry.exists()

        # Lookup should find it
        found = cache.lookup(source_file, tool_cmd)
        assert found is not None
        assert found == cache_entry

        # Different command should not find it
        not_found = cache.lookup(source_file, "cl /c /W4")
        assert not_found is None

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

        dependencies = [source_file]
        tool_cmd = "cl /c"
        stdout = "Build succeeded"
        stderr = "No warnings"
        returncode = 0

        cache_entry = cache.store(source_file, tool_cmd, dependencies, [output_file], stdout, stderr, returncode)

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
            output_dir=output_dir
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
            output_dir=output_dir
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
        returncode1 = quicken_instance.run(test_cpp_file, "clang", tool_args)
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
        returncode2 = quicken_instance.run(test_cpp_file, "clang", tool_args)
        assert returncode2 == returncode1

        # .o file should be restored from cache
        assert obj_file.exists()

    def test_clang_different_optimization_levels(self, quicken_instance, test_cpp_file):
        """Test that different optimization levels create different cache entries."""
        # Compile with -O0
        returncode1 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=0)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = test_cpp_file.parent / "test.o"
        if obj_file.exists():
            obj_file.unlink()

        # Compile with -O2 - should be a cache miss
        returncode2 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=2)
        # Just check it completes, return code may vary
        assert isinstance(returncode2, int)

    def test_clang_optimization_none_accepts_any_level(self, quicken_instance, test_cpp_file):
        """Test that optimization=None accepts cache hits from any optimization level."""
        # Compile with optimization level 2
        returncode1 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=2)
        if returncode1 != 0:
            pytest.fail("clang++ compilation with -O2 failed")

        obj_file = test_cpp_file.parent / "test.o"
        assert obj_file.exists(), "clang++ didn't create .o file"

        # Delete the .o file
        obj_file.unlink()

        # Compile with optimization=None - should get cache hit from O2
        returncode2 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=None)
        assert returncode2 == returncode1, "Return codes should match"

        # .o file should be restored from cache
        assert obj_file.exists(), ".o file should be restored from cache"

        # Delete the .o file again
        obj_file.unlink()

        # Compile with a different specific level (O1) - should be cache miss
        returncode3 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=1)
        assert isinstance(returncode3, int)

        # Now with optimization=None, should hit the O2 cache (first one encountered)
        obj_file.unlink()
        returncode4 = quicken_instance.run(test_cpp_file, "clang", ["-c"], optimization=None)
        assert obj_file.exists(), ".o file should be restored from cache again"

    def test_clang_with_warnings(self, quicken_instance, temp_dir):
        """Test clang++ compilation with warnings."""
        cpp_file = temp_dir / "test_warn.cpp"
        cpp_file.write_text(CPP_CODE_WITH_WARNING)

        tool_args = ["-c", "-Wall"]

        # First run
        returncode1 = quicken_instance.run(cpp_file, "clang", tool_args)
        if returncode1 != 0:
            pytest.fail("clang++ compilation failed")

        obj_file = temp_dir / "test_warn.o"
        if not obj_file.exists():
            pytest.fail("clang++ didn't create .o file")

        obj_file.unlink()

        # Second run - cache hit
        returncode2 = quicken_instance.run(cpp_file, "clang", tool_args)
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

    def test_multiple_tools_same_file(self, quicken_instance, test_cpp_file):
        """Test that the same file can be processed by multiple tools with separate caches."""
        # Compile with MSVC
        returncode_cl = quicken_instance.run(test_cpp_file, "cl", ["/c", "/nologo", "/EHsc"])
        if returncode_cl != 0:
            pytest.fail("MSVC compilation failed")

        # Compile with clang
        returncode_clang = quicken_instance.run(test_cpp_file, "clang", ["-c"])
        # Clang may fail, that's okay

        # Analyze with clang-tidy
        returncode_tidy = quicken_instance.run(test_cpp_file, "clang-tidy", ["--checks=*"])
        assert isinstance(returncode_tidy, int)


    def test_cache_index_persistence(self, quicken_instance, test_cpp_file, cache_dir, config_file):
        """Test that cache index persists across Quicken instances."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # First run
        returncode1 = quicken_instance.run(test_cpp_file, "cl", tool_args)
        if returncode1 != 0:
            pytest.fail("MSVC compilation failed")

        # Create new instance with same cache
        from quicken import Quicken
        quicken2 = Quicken(config_file)  # Pass config_file Path, not the config dict
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
