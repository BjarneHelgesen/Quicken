#!/usr/bin/env python3
"""
Deep profiling of cache hit happy path to identify optimization opportunities.

Run 1: Cache miss (populates cache)
Run 2: Hash match (Quicken updates mtime in cache)
Run 3: mtime/size match (fast path, no hashing)
"""

import shutil
import tempfile
from pathlib import Path

from quicken import Quicken


def create_simple_project(temp_dir: Path, num_headers: int, num_main_files: int = 10):
    """Create a simple C++ project with multiple copies of the main file."""
    headers = []
    for i in range(num_headers):
        header = temp_dir / f"header{i}.h"
        header.write_text(f"// Header {i}\nclass Class{i} {{ int value = {i}; }};\n")
        headers.append(header)

    includes = "\n".join([f'#include "header{i}.h"' for i in range(num_headers)])

    # Create multiple copies of the main file to avoid output file contention
    main_files = []
    for i in range(num_main_files):
        main_cpp = temp_dir / f"main{i}.cpp"
        main_cpp.write_text(f"{includes}\nint main() {{ return {i}; }}\n")
        main_files.append(main_cpp)

    return main_files, headers


def run_happy_path_test(quicken: Quicken, main_files: list[Path], num_iterations: int = 100):
    """Run consecutive cache hits cycling through files to avoid output file contention."""
    for i in range(num_iterations):
        main_cpp = main_files[i % len(main_files)]
        quicken.cl(["/c", "/nologo", "/EHsc"], [], [], optimization=0)(main_cpp)


def main ():
    """Profile a cache hit scenario ."""

    num_headers = 40
    num_main_files = 20
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        main_files, headers = create_simple_project(temp_dir, num_headers, num_main_files)

        # Copy tools.json to ~/.quicken/tools.json
        config_file = Path(__file__).parent / "tools.json"
        quicken_dir = Path.home() / ".quicken"
        quicken_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(config_file, quicken_dir / "tools.json")

        quicken = Quicken(temp_dir)

        # First run: cache miss for each file (populates cache)
        for main_cpp in main_files:
            quicken.cl(["/c", "/nologo", "/EHsc"], [], [], optimization=0)(main_cpp)

        # Run the happy path test
        run_happy_path_test(quicken, main_files)


if __name__ == "__main__":
    main()
