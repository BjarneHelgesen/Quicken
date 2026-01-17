#!/usr/bin/env python3
"""
Regression test for Quicken working directory bug with subdirectory source files.

Bug: When a source file is located in a subdirectory and a relative path argument
is passed to the tool, Quicken sets cwd=source_file.parent instead of cwd=repo_dir,
causing the tool to fail to find files specified by relative paths.

Example: Doxygen called with:
  - source_file: /repo/.doxygen/Doxyfile.xml (absolute)
  - tool_args: [".doxygen/Doxyfile.xml"] (relative to repo)
  - Current behavior: cwd=/repo/.doxygen, Doxygen looks for .doxygen/.doxygen/Doxyfile.xml → FAIL
  - Expected behavior: cwd=/repo, Doxygen looks for .doxygen/Doxyfile.xml → SUCCESS

Root Cause: In quicken.py:972, subprocess.run() uses cwd=source_file.parent
Expected: subprocess.run() should use cwd=repo_dir

Original behavior from run_repo_tool() (commit 2a05fe2): cwd=repo_dir
Broken by refactoring (commit 7ee11b7): changed to cwd=source_file.parent

Impact: Breaks Doxygen and any other tool that expects to run from repo root
        with relative path arguments.
"""

import pytest
from pathlib import Path
from quicken import Quicken


# Minimal Doxyfile that processes a single C++ file
DOXYFILE_TEMPLATE = """
PROJECT_NAME           = "TestProject"
OUTPUT_DIRECTORY       = "{output_dir}"
INPUT                  = "{input_dir}"
RECURSIVE              = NO
FILE_PATTERNS          = *.cpp
EXTRACT_ALL            = YES
GENERATE_HTML          = NO
GENERATE_LATEX         = NO
GENERATE_XML           = YES
XML_OUTPUT             = xml
QUIET                  = YES
WARNINGS               = NO
"""

SIMPLE_CPP = """
// Simple C++ file for Doxygen to process
int main() {
    return 0;
}
"""


@pytest.mark.regression_test
def test_subdirectory_source_file_with_relative_args(temp_dir):
    """
    Verify that tools can be run with source files in subdirectories
    when relative path arguments are used.

    This reproduces the LevelUp Doxygen failure where:
    1. Doxyfile is in .doxygen/ subdirectory
    2. Quicken is called with relative path argument ".doxygen/Doxyfile.xml"
    3. Tool should execute from repo root, not from .doxygen/

    Expected: Doxygen runs successfully and generates XML output
    Actual (BUG): Doxygen fails with "configuration file not found"
    """
    quicken = Quicken(temp_dir)
    quicken.clear_cache()

    # Create directory structure similar to LevelUp:
    # repo/
    #   test.cpp
    #   .doxygen/
    #     Doxyfile.xml
    #     xml/  (output directory, created by Doxygen)

    doxygen_dir = temp_dir / ".doxygen"
    doxygen_dir.mkdir(parents=True, exist_ok=True)

    # Create minimal C++ file to process
    cpp_file = temp_dir / "test.cpp"
    cpp_file.write_text(SIMPLE_CPP)

    # Create Doxyfile in subdirectory
    doxyfile_path = doxygen_dir / "Doxyfile.xml"
    doxyfile_content = DOXYFILE_TEMPLATE.format(
        output_dir=str(doxygen_dir).replace('\\', '/'),
        input_dir=str(temp_dir).replace('\\', '/')
    )
    doxyfile_path.write_text(doxyfile_content)

    # Call Quicken with ABSOLUTE path for source_file
    # and RELATIVE path in tool_args (relative to repo_dir)
    # This mirrors how LevelUp calls Quicken:
    #   quicken.run(doxyfile_path, "doxygen", [str(doxyfile_relative)])
    doxyfile_relative = doxyfile_path.relative_to(temp_dir)

    _, _, returncode = quicken.run(
        doxyfile_path,           # Absolute: /repo/.doxygen/Doxyfile.xml
        "doxygen",
        [str(doxyfile_relative)], # Relative: .doxygen/Doxyfile.xml
        [],
        []
    )

    # Verify Doxygen succeeded
    assert returncode == 0, \
        f"Doxygen failed with returncode {returncode}. " \
        f"BUG: Tool executed from wrong working directory. " \
        f"Expected cwd={temp_dir}, but tool couldn't find file at relative path {doxyfile_relative}"

    # Verify XML output was generated
    xml_output_dir = doxygen_dir / "xml"
    assert xml_output_dir.exists(), \
        f"Doxygen XML output not generated at {xml_output_dir}. " \
        f"BUG: cwd=source_file.parent instead of cwd=repo_dir"

    # Verify index.xml exists (created by Doxygen)
    index_xml = xml_output_dir / "index.xml"
    assert index_xml.exists(), \
        f"Doxygen index.xml not found. Output directory exists but is empty."


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "regression_test"])
