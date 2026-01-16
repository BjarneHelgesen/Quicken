#!/usr/bin/env python3
"""
Regression test for relative path validation bug.

Bug: RepoPath refactoring broke validation of relative paths pointing outside the repo.
Commit: (current uncommitted changes)
Fixed by: Adding .resolve() calls before RepoPath construction
"""

import pytest
from pathlib import Path
from quicken import Quicken


# Simple C++ code for testing
SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""


@pytest.mark.regression_test
def test_relative_path_outside_repo_rejected(temp_dir):
    """
    Bug description: Relative paths pointing outside repo are not validated.

    Category: Security/correctness issue

    Steps to reproduce:
    1. Create a repo directory with a source file
    2. Create a file OUTSIDE the repo
    3. Call Quicken.run() with a relative path like "../outside.cpp" pointing to the external file

    Expected behavior: ValueError raised because file is outside repo
    Actual behavior (BUGGY): Path is accepted, file outside repo is processed

    Root cause:
    - RepoPath constructor expects resolved paths but run() doesn't resolve before calling
    - Relative paths like "../outside.cpp" bypass validation
    - The path gets stored as-is, and toAbsolutePath() creates repo_dir / "../outside.cpp"

    Fix: Call source_file.resolve() before creating RepoPath in run() and run_repo_tool()
    """
    # Create repo directory with a source file
    repo_dir = temp_dir / "repo"
    repo_dir.mkdir()
    inside_file = repo_dir / "inside.cpp"
    inside_file.write_text(SIMPLE_CPP_CODE)

    # Create Quicken instance for the repo
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Create a file OUTSIDE the repo
    outside_dir = temp_dir / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.cpp"
    outside_file.write_text(SIMPLE_CPP_CODE)

    # Verify the outside file is actually outside the repo
    assert not outside_file.is_relative_to(repo_dir), "Test setup error: outside_file should be outside repo"

    # Try to compile with a relative path that points outside the repo
    # This path resolves to: repo_dir / "../outside/outside.cpp" = temp_dir / "outside/outside.cpp"
    relative_path_to_outside = Path("../outside/outside.cpp")

    # This SHOULD raise ValueError but currently doesn't (BUG)
    with pytest.raises(ValueError, match="(outside repository|not in the subpath)"):
        quicken.run(
            relative_path_to_outside,
            "cl",
            ["/c", "/nologo", "/EHsc"],
            [],
            [])


@pytest.mark.regression_test
def test_relative_path_inside_repo_accepted(temp_dir):
    """
    Verify that valid relative paths inside the repo work correctly.

    This is the complement to test_relative_path_outside_repo_rejected.
    Relative paths that resolve to files inside the repo should work.
    """
    # Create repo with subdirectory
    repo_dir = temp_dir / "repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()

    source_file = src_dir / "main.cpp"
    source_file.write_text(SIMPLE_CPP_CODE)

    # Create Quicken instance for the repo
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Use relative path from repo root: "src/main.cpp"
    relative_path = Path("src/main.cpp")

    # This SHOULD work (and currently might, but let's verify)
    returncode = quicken.run(
        relative_path,
        "cl",
        ["/c", "/nologo", "/EHsc"],
        [],
        [])

    # Should succeed (0 or whatever the compilation returns)
    assert isinstance(returncode, int), "Should complete without ValueError"


@pytest.mark.regression_test
def test_relative_path_with_dotdot_inside_repo(temp_dir):
    """
    Verify that relative paths with ../ that stay inside repo work correctly.

    Example: repo/src/../lib/util.cpp resolves to repo/lib/util.cpp
    This should be accepted if it stays inside the repo.
    """
    # Create repo structure:
    # repo/
    #   src/
    #   lib/
    #     util.cpp
    repo_dir = temp_dir / "repo"
    repo_dir.mkdir()
    src_dir = repo_dir / "src"
    src_dir.mkdir()
    lib_dir = repo_dir / "lib"
    lib_dir.mkdir()

    util_file = lib_dir / "util.cpp"
    util_file.write_text(SIMPLE_CPP_CODE)

    # Create Quicken instance for the repo
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Use relative path with ../: "src/../lib/util.cpp" -> "lib/util.cpp"
    relative_path = Path("src/../lib/util.cpp")

    # This SHOULD work
    returncode = quicken.run(
        relative_path,
        "cl",
        ["/c", "/nologo", "/EHsc"],
        [],
        [])

    assert isinstance(returncode, int), "Should complete without ValueError"


@pytest.mark.regression_test
def test_absolute_path_outside_repo_rejected(temp_dir):
    """
    Verify that absolute paths outside the repo are rejected.

    This should already work, but let's ensure it's not broken.
    """
    # Create repo directory
    repo_dir = temp_dir / "repo"
    repo_dir.mkdir()

    # Create Quicken instance for the repo
    quicken = Quicken(repo_dir)
    quicken.clear_cache()

    # Create file outside repo
    outside_dir = temp_dir / "outside"
    outside_dir.mkdir()
    outside_file = outside_dir / "outside.cpp"
    outside_file.write_text(SIMPLE_CPP_CODE)

    # Try with absolute path to outside file
    with pytest.raises(ValueError, match="(outside repository|not in the subpath)"):
        quicken.run(
            outside_file,  # Absolute path
            "cl",
            ["/c", "/nologo", "/EHsc"],
            [],
            [])


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "regression_test"])
