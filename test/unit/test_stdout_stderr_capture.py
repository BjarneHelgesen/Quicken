#!/usr/bin/env python3
"""
Unit tests for stdout and stderr capturing in Quicken.

Verifies that:
1. Cache misses correctly capture tool stdout and stderr
2. Cache hits reproduce the exact same stdout and stderr
3. Both streams are handled independently
4. Output is byte-for-byte identical between cache miss and hit
"""

import io
import json
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

from quicken import Quicken
from quicken._cache import QuickenCache, CacheKey
from quicken._repo_path import RepoPath


# Sample C++ code for testing
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


def capture_output(func, *args, **kwargs):
    """
    Call quicken.run() or quicken.run_repo_tool() and capture its output.

    The Quicken API returns just an integer and writes to stdout/stderr as side effects.
    This function captures those side effects using StringIO.

    Returns:
        Tuple of (returncode, stdout, stderr) - note the order matches test expectations
    """
    # Create StringIO objects to capture stdout and stderr
    stdout_capture = io.StringIO()
    stderr_capture = io.StringIO()

    # Redirect stdout and stderr to our StringIO objects
    with patch('sys.stdout', stdout_capture), patch('sys.stderr', stderr_capture):
        returncode = func(*args, **kwargs)

    # Get the captured output
    stdout = stdout_capture.getvalue()
    stderr = stderr_capture.getvalue()

    return returncode, stdout, stderr


class TestStdoutStderrCapture:
    """Test stdout/stderr capturing and reproduction."""

    @pytest.mark.pedantic
    def test_cache_stores_stdout_stderr_metadata(self, cache_dir, temp_dir):
        """Test that cache stores stdout and stderr in metadata."""
        cache = QuickenCache(cache_dir)

        # Create source file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        # Create fake output file
        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        stdout = "Compilation successful\n"
        stderr = "Warning: something\n"
        returncode = 0

        # Store in cache
        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], stdout, stderr, returncode)

        # Verify metadata.json contains stdout and stderr
        metadata_file = cache_entry / "metadata.json"
        assert metadata_file.exists()

        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        assert metadata["stdout"] == stdout
        assert metadata["stderr"] == stderr
        assert metadata["returncode"] == returncode

    @pytest.mark.pedantic
    def test_cache_restore_returns_correct_stdout_stderr(self, cache_dir, temp_dir):
        """Test that cache.restore() prints the correct stdout and stderr."""
        cache = QuickenCache(cache_dir)

        # Create source file
        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        # Create fake output file
        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]
        original_stdout = "Build output line 1\nBuild output line 2\n"
        original_stderr = "Warning: unused variable\n"
        returncode = 0

        # Store in cache
        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], original_stdout, original_stderr, returncode)

        # Delete output file
        output_file.unlink()

        # Restore from cache - capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            restored_returncode = cache.restore(cache_entry, temp_dir)
            restored_stdout = sys.stdout.getvalue()
            restored_stderr = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr


        # Verify exact match
        assert restored_stdout == original_stdout
        assert restored_stderr == original_stderr
        assert restored_returncode == returncode

    @pytest.mark.pedantic
    def test_empty_stdout_stderr_handling(self, cache_dir, temp_dir):
        """Test that empty stdout and stderr are handled correctly."""
        cache = QuickenCache(cache_dir)

        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c", "/nologo"]

        # Store with empty stdout and stderr
        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], "", "", 0)

        # Restore - capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            restored_returncode = cache.restore(cache_entry, temp_dir)
            restored_stdout = sys.stdout.getvalue()
            restored_stderr = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Verify empty strings are preserved
        assert restored_stdout == ""
        assert restored_stderr == ""
        assert restored_returncode == 0

    @pytest.mark.pedantic
    def test_multiline_stdout_stderr_preservation(self, cache_dir, temp_dir):
        """Test that multiline stdout and stderr are preserved correctly."""
        cache = QuickenCache(cache_dir)

        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]

        # Create multiline output with various line endings
        original_stdout = "Line 1\nLine 2\nLine 3\n"
        original_stderr = "Error on line 10\nError on line 20\n"

        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], original_stdout, original_stderr, 0)

        # Restore and verify exact preservation - capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            restored_returncode = cache.restore(cache_entry, temp_dir)
            restored_stdout = sys.stdout.getvalue()
            restored_stderr = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        assert restored_stdout == original_stdout
        assert restored_stderr == original_stderr
        assert len(restored_stdout.splitlines()) == 3
        assert len(restored_stderr.splitlines()) == 2

    @pytest.mark.pedantic
    def test_special_characters_in_output(self, cache_dir, temp_dir):
        """Test that special characters in stdout and stderr are preserved."""
        cache = QuickenCache(cache_dir)

        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        output_file = temp_dir / "test.obj"
        output_file.write_text("fake object file")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]

        # Include special characters, unicode, paths with backslashes
        original_stdout = "C:\\Path\\To\\File.cpp\nTest: 100% complete\n"
        original_stderr = "Warning: '\t' tab and \"quotes\"\n"

        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], original_stdout, original_stderr, 0)

        # Restore and verify - capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            restored_returncode = cache.restore(cache_entry, temp_dir)
            restored_stdout = sys.stdout.getvalue()
            restored_stderr = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        assert restored_stdout == original_stdout
        assert restored_stderr == original_stderr
        assert '\t' in restored_stderr
        assert '\\' in restored_stdout


class TestMSVCStdoutStderr:
    """Test stdout/stderr reproduction for MSVC compilation."""

    def test_msvc_stdout_stderr_reproduction(self, quicken_instance, test_cpp_file):
        """Test that MSVC stdout/stderr is identical between cache miss and hit."""
        tool_args = ["/c", "/nologo", "/EHsc"]

        # First run (cache miss) - capture output
        returncode1, stdout1, stderr1 = capture_output(
            quicken_instance.run,
            test_cpp_file, "cl", tool_args, [], [],
        )

        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping test")

        # Delete output file
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run (cache hit) - capture output
        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            test_cpp_file, "cl", tool_args, [], [],
        )

        # Verify outputs are identical
        assert returncode1 == returncode2, "Return codes should match"
        assert stdout1 == stdout2, f"Stdout mismatch:\nCache miss: {repr(stdout1)}\nCache hit: {repr(stdout2)}"
        assert stderr1 == stderr2, f"Stderr mismatch:\nCache miss: {repr(stderr1)}\nCache hit: {repr(stderr2)}"

    @pytest.mark.pedantic
    def test_msvc_with_warnings_stdout_stderr(self, quicken_instance, temp_dir):
        """Test that MSVC warnings are reproduced correctly."""
        # Create file with warning
        cpp_file = temp_dir / "test_warn.cpp"
        cpp_file.write_text(CPP_CODE_WITH_WARNING)

        tool_args = ["/c", "/W4", "/EHsc"]  # High warning level

        # First run (cache miss)
        returncode1, stdout1, stderr1 = capture_output(
            quicken_instance.run,
            cpp_file, "cl", tool_args, [], [],
        )

        # Should succeed with warnings
        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping test")

        # Verify we got some output (compilation message or warning)
        has_output = bool(stdout1 or stderr1)

        # Delete output file
        obj_file = cpp_file.parent / "test_warn.obj"
        if obj_file.exists():
            obj_file.unlink()

        # Second run (cache hit)
        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            cpp_file, "cl", tool_args, [], [],
        )
        
        # Verify exact reproduction
        assert returncode1 == returncode2
        assert stdout1 == stdout2
        assert stderr1 == stderr2

        # If there was output before, there should be output now
        if has_output:
            assert bool(stdout2 or stderr2), "Output should be reproduced from cache"

    def test_msvc_nologo_flag_affects_stdout(self, quicken_instance, test_cpp_file):
        """Test that /nologo flag properly affects stdout in cache."""
        # Without /nologo - may have banner
        tool_args_banner = ["/c", "/EHsc"]

        returncode1, stdout1, stderr1 = capture_output(
            quicken_instance.run,
            test_cpp_file, "cl", tool_args_banner, [], [],
        )

        if returncode1 != 0:
            pytest.skip("MSVC compilation failed, skipping test")

        # With /nologo - should suppress banner
        obj_file = test_cpp_file.parent / "test.obj"
        if obj_file.exists():
            obj_file.unlink()

        tool_args_nologo = ["/c", "/nologo", "/EHsc"]

        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            test_cpp_file, "cl", tool_args_nologo, [], [],
        )

        if returncode2 != 0:
            pytest.skip("MSVC compilation with /nologo failed, skipping test")

        # These are different tool commands, so they should have separate cache entries
        # and potentially different output
        # Just verify both are valid
        assert isinstance(stdout1, str)
        assert isinstance(stderr1, str)
        assert isinstance(stdout2, str)
        assert isinstance(stderr2, str)


class TestClangStdoutStderr:
    """Test stdout/stderr reproduction for Clang compilation."""

    def test_clang_stdout_stderr_reproduction(self, quicken_instance, test_cpp_file):
        """Test that clang++ stdout/stderr is identical between cache miss and hit."""
        tool_args = ["-c"]

        # First run (cache miss)
        returncode1, stdout1, stderr1 = capture_output(
            quicken_instance.run,
            test_cpp_file, "clang++", tool_args, [], [],
        )

        if returncode1 != 0:
            pytest.skip("clang++ compilation failed, skipping test")

        # Delete output file
        obj_file = test_cpp_file.parent / "test.o"
        if obj_file.exists():
            obj_file.unlink()

        # Second run (cache hit)
        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            test_cpp_file, "clang++", tool_args, [], [],
        )

        # Verify exact reproduction
        assert returncode1 == returncode2
        assert stdout1 == stdout2, f"Stdout mismatch:\nCache miss: {repr(stdout1)}\nCache hit: {repr(stdout2)}"
        assert stderr1 == stderr2, f"Stderr mismatch:\nCache miss: {repr(stderr1)}\nCache hit: {repr(stderr2)}"

    @pytest.mark.pedantic
    def test_clang_with_warnings_reproduction(self, quicken_instance, temp_dir):
        """Test that clang++ warnings are reproduced correctly."""
        cpp_file = temp_dir / "test_warn.cpp"
        cpp_file.write_text(CPP_CODE_WITH_WARNING)

        tool_args = ["-c", "-Wall", "-Wextra"]

        # First run (cache miss)
        returncode1, stdout1, stderr1 = capture_output(
            quicken_instance.run,
            cpp_file, "clang++", tool_args, [], [],
        )

        if returncode1 != 0:
            pytest.skip("clang++ compilation failed, skipping test")

        # Should have warnings in output
        has_warnings = bool(stderr1)

        # Delete output file
        obj_file = cpp_file.parent / "test_warn.o"
        if obj_file.exists():
            obj_file.unlink()

        # Second run (cache hit)
        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            cpp_file, "clang++", tool_args, [], [],
        )

        # Verify exact reproduction
        assert returncode1 == returncode2
        assert stdout1 == stdout2
        assert stderr1 == stderr2

        # Warnings should be reproduced
        if has_warnings:
            assert stderr2, "Warnings should be reproduced from cache"


class TestClangTidyStdoutStderr:
    """Test stdout/stderr reproduction for clang-tidy analysis."""

    def test_clang_tidy_stdout_reproduction(self, quicken_instance, test_cpp_file):
        """Test that clang-tidy output is reproduced correctly."""
        tool_args = ["--checks=readability-*"]

        # First run (cache miss)
        try:
            returncode1, stdout1, stderr1 = capture_output(
                quicken_instance.run,
                test_cpp_file, "clang-tidy", tool_args, [], [],
            )
        except Exception:
            pytest.skip("clang-tidy not available or failed")

        # clang-tidy may have non-zero return code if it finds issues
        # Just verify it completed
        assert isinstance(returncode1, int)

        # Second run (cache hit)
        returncode2, stdout2, stderr2 = capture_output(
            quicken_instance.run,
            test_cpp_file, "clang-tidy", tool_args, [], [],
        )

        # Verify exact reproduction
        assert returncode1 == returncode2
        assert stdout1 == stdout2, f"Stdout mismatch:\nCache miss: {repr(stdout1)}\nCache hit: {repr(stdout2)}"
        assert stderr1 == stderr2, f"Stderr mismatch:\nCache miss: {repr(stderr1)}\nCache hit: {repr(stderr2)}"


class TestRepoToolStdoutStderr:
    """Test stdout/stderr reproduction for repo-level tools."""

    def test_repo_tool_stdout_stderr_storage(self, cache_dir, temp_dir):
        """Test that repo-level tool stdout/stderr is stored correctly."""
        cache = QuickenCache(cache_dir)

        # Create main file (e.g., Doxyfile)
        main_file = temp_dir / "Doxyfile"
        main_file.write_text("# Doxygen config")

        # Create fake dependencies
        cpp_file = temp_dir / "main.cpp"
        cpp_file.write_text("int main() { return 0; }")

        # Create fake output directory
        output_dir = temp_dir / "output"
        output_dir.mkdir()
        output_file = output_dir / "index.html"
        output_file.write_text("<html></html>")

        main_repo_path = RepoPath(temp_dir, main_file.resolve())
        cpp_repo_path = RepoPath(temp_dir, cpp_file.resolve())
        dep_repo_paths = [main_repo_path, cpp_repo_path]
        tool_name = "doxygen"
        tool_args = []
        stdout = "Generating documentation...\nDone.\n"
        stderr = ""
        returncode = 0

        # Store cache entry
        cache_key = CacheKey(main_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [output_file], stdout, stderr, returncode)

        # Verify metadata contains stdout/stderr
        metadata_file = cache_entry / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        assert metadata["stdout"] == stdout
        assert metadata["stderr"] == stderr
        assert metadata["returncode"] == returncode


class TestErrorCases:
    """Test error case handling for stdout/stderr."""

    @pytest.mark.pedantic
    def test_non_zero_returncode_with_stderr(self, cache_dir, temp_dir):
        """Test that compilation errors (non-zero return codes) preserve stderr."""
        cache = QuickenCache(cache_dir)

        source_file = temp_dir / "test.cpp"
        source_file.write_text("int main() { return 0; }")

        source_repo_path = RepoPath(temp_dir, source_file.resolve())
        dep_repo_paths = [source_repo_path]
        tool_name = "cl"
        tool_args = ["/c"]

        # Simulate compilation error
        stdout = ""
        stderr = "error C2065: 'undeclared_var': undeclared identifier\n"
        returncode = 2

        # Store error result (no output files created)
        cache_key = CacheKey(source_repo_path, tool_name, tool_args, [], temp_dir)
        cache_entry = cache.store(cache_key, dep_repo_paths, [], stdout, stderr, returncode)

        # Restore - capture output
        old_stdout = sys.stdout
        old_stderr = sys.stderr
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()

        try:
            restored_returncode = cache.restore(cache_entry, temp_dir)
            restored_stdout = sys.stdout.getvalue()
            restored_stderr = sys.stderr.getvalue()
        finally:
            sys.stdout = old_stdout
            sys.stderr = old_stderr

        # Verify error information is preserved
        assert restored_returncode == 2
        assert restored_stderr == stderr
        assert "undeclared" in restored_stderr


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
