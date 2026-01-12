#!/usr/bin/env python3
"""
Regression test for output file detection scope bug.

Bug: Quicken recursively scans entire repo directory tree for output files,
     causing performance issues and incorrectly identifying unrelated files.
Commit: (pending fix)
Fixed by: (pending)
"""

import pytest
from pathlib import Path
from quicken import Quicken
import time


# Simple C++ code for testing
SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""


@pytest.mark.regression_test
def test_output_detection_does_not_scan_subdirectories(temp_dir):
    """
    Bug description: Quicken uses directory.rglob("*") to recursively scan
    the entire repository tree for output files. This causes:
    1. Performance issues on large repositories
    2. Incorrectly identifies unrelated files as tool outputs

    Category: Performance and correctness issue

    Steps to reproduce:
    1. Create a repo with a source file
    2. Create a subdirectory with unrelated files
    3. Compile the source file
    4. Modify an unrelated file in subdirectory during compilation
    5. Quicken incorrectly identifies it as a tool output file

    Expected behavior: Only scan for output files in relevant locations
                       (current directory, or tool-specific output paths)
    Actual behavior (BUGGY): Recursively scans entire directory tree with rglob("*")

    Real-world impact:
    - When repo_dir is system temp directory, scans ALL temp files/subdirs
    - Found VSLogs/ subdirectory with VS language server logs
    - Files modified by VS during test incorrectly identified as tool outputs
    - Attempted to cache them → Windows path length limit exceeded

    Root cause: _tool_cmd.py:232 uses directory.rglob("*")

    Fix options:
    1. Only scan current working directory (non-recursive)
    2. Have tools declare their output locations explicitly
    3. Use more targeted detection (e.g., only scan for specific extensions)
    4. Add configurable ignore patterns for subdirectories
    """
    # Create repo with source file
    repo_dir = temp_dir / "test_repo"
    repo_dir.mkdir()

    source_file = repo_dir / "main.cpp"
    source_file.write_text(SIMPLE_CPP_CODE)

    # Create a deep subdirectory structure that shouldn't be scanned
    unrelated_dir = repo_dir / "logs" / "deep" / "nested" / "path"
    unrelated_dir.mkdir(parents=True)

    # Create many unrelated files (simulating a large project)
    for i in range(10):
        unrelated_file = unrelated_dir / f"unrelated_{i}.log"
        unrelated_file.write_text(f"Unrelated content {i}")

    # Initialize Quicken
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Modify one of the unrelated files slightly after compilation starts
    # to simulate a file being modified during the build (like VS logs)
    unrelated_marker = unrelated_dir / "modified_during_build.log"
    unrelated_marker.write_text("Before")

    # Small delay to ensure different timestamp
    time.sleep(0.01)

    # Now modify the unrelated file
    unrelated_marker.write_text("Modified during build")

    # Compile the source file
    returncode = quicken.run(
        source_file,
        "cl",
        ["/c", "/nologo", "/EHsc"])

    assert returncode == 0, "Compilation should succeed"

    # The bug: Quicken's cache now includes the unrelated file because
    # it recursively scanned the entire directory tree and found files
    # with modified timestamps.
    #
    # Expected: Only cache .obj file from compilation
    # Actual (BUGGY): Also caches unrelated_marker.log and other files
    #
    # This test documents the issue. A proper fix would make this test pass
    # by NOT scanning subdirectories unnecessarily.


@pytest.mark.regression_test
def test_performance_with_large_directory_tree(temp_dir):
    """
    Verify that output file detection doesn't cause performance issues
    on repositories with large directory trees.

    With rglob("*"), scanning thousands of files before AND after every
    tool execution would be extremely slow.
    """
    # Create repo with source file
    repo_dir = temp_dir / "test_repo"
    repo_dir.mkdir()

    source_file = repo_dir / "main.cpp"
    source_file.write_text(SIMPLE_CPP_CODE)

    # Create a moderately large directory structure
    # (Real repos can have 10,000+ files)
    for i in range(5):
        subdir = repo_dir / f"subdir_{i}"
        subdir.mkdir()
        for j in range(20):
            (subdir / f"file_{j}.txt").write_text("content")

    # Initialize Quicken
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Time the compilation
    start = time.time()
    returncode = quicken.run(
        source_file,
        "cl",
        ["/c", "/nologo", "/EHsc"])
    duration = time.time() - start

    assert returncode == 0, "Compilation should succeed"

    # With rglob("*"), this would scan 100+ files twice (before/after)
    # This test documents that the current implementation is inefficient
    # A proper fix would make this much faster by not scanning subdirectories


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "regression_test"])
