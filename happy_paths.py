#!/usr/bin/env python3
"""
Deep profiling of cache hit happy path to identify optimization opportunities.

Run 1: Cache miss (populates cache)
Run 2: Hash match (Quicken updates mtime in cache)
Run 3: mtime/size match (fast path, no hashing)
"""

import tempfile
from pathlib import Path

from quicken import Quicken


def create_simple_project(temp_dir: Path, num_headers: int):
    """Create a simple C++ project."""
    headers = []
    for i in range(num_headers):
        header = temp_dir / f"header{i}.h"
        header.write_text(f"// Header {i}\nclass Class{i} {{ int value = {i}; }};\n")
        headers.append(header)

    includes = "\n".join([f'#include "header{i}.h"' for i in range(num_headers)])
    main_cpp = temp_dir / "main.cpp"
    main_cpp.write_text(f"{includes}\nint main() {{ return 0; }}\n")

    return main_cpp, headers



def main ():
    """Profile a cache hit scenario ."""

    num_headers = 100
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        main_cpp, headers = create_simple_project(temp_dir, num_headers)

        config_file = Path(__file__).parent / "tools.json"
        quicken = Quicken(config_file, temp_dir)

        #First run: cache hit after hashing all files
        quicken.run(main_cpp, "cl", ["/c", "/nologo", "/EHsc"])

        #Consecutive runs: cache hit after mtime/size check for all files.
        for _ in range(199):
            quicken.run(main_cpp, "cl", ["/c", "/nologo", "/EHsc"])


if __name__ == "__main__":
    main()
