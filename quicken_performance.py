#!/usr/bin/env python3
"""
Performance test for Quicken caching across multiple tools.
Measures cache miss vs cache hit timing for MSVC, Clang, and clang-tidy.
"""

import tempfile
import time
from pathlib import Path

from quicken import Quicken, QuickenCache


SIMPLE_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""


def test_tool(quicken, cpp_file, tool_name, tool_args, expected_outputs):
    """
    Test a single tool's cache performance.

    Args:
        quicken: Quicken instance
        cpp_file: Path to C++ source file
        tool_name: Name of tool (cl, clang, clang-tidy)
        tool_args: List of arguments for the tool
        expected_outputs: List of expected output files (empty for analysis-only tools)

    Returns:
        tuple: (miss_time, hit_time, speedup) or None if test failed
    """
    print(f"\n{'='*60}")
    print(f"Testing {tool_name}")
    print('='*60)

    # Cache MISS - First run
    print(f"[{tool_name}] Running cache MISS test...")
    start = time.time()
    returncode1 = quicken.run(cpp_file, tool_name, tool_args)
    miss_time = time.time() - start

    if returncode1 != 0:
        print(f"[{tool_name}] WARNING: Tool failed with return code {returncode1}")
        print(f"[{tool_name}] Skipping this tool.")
        return None

    # Verify expected outputs exist
    for output_file in expected_outputs:
        if not output_file.exists():
            print(f"[{tool_name}] WARNING: Expected output {output_file.name} not created")
            print(f"[{tool_name}] Skipping this tool.")
            return None

    # Delete output files to test cache restoration
    for output_file in expected_outputs:
        output_file.unlink()

    # Cache HIT - Second run
    print(f"[{tool_name}] Running cache HIT test...")
    start = time.time()
    returncode2 = quicken.run(cpp_file, tool_name, tool_args)
    hit_time = time.time() - start

    if returncode2 != 0:
        print(f"[{tool_name}] ERROR: Cache hit failed with return code {returncode2}")
        return None

    # Verify outputs restored from cache
    for output_file in expected_outputs:
        if not output_file.exists():
            print(f"[{tool_name}] ERROR: Output {output_file.name} not restored from cache")
            return None

    speedup = miss_time / hit_time if hit_time > 0 else 0
    print(f"[{tool_name}] ✓ Cache MISS: {miss_time:.3f}s")
    print(f"[{tool_name}] ✓ Cache HIT:  {hit_time:.3f}s")
    print(f"[{tool_name}] ✓ Speedup:    {speedup:.1f}x")

    return (miss_time, hit_time, speedup)


def main():
    # Setup
    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)

        # Create test file
        cpp_file = temp_dir / "test.cpp"
        cpp_file.write_text(SIMPLE_CPP_CODE)

        # Create cache directory
        cache_dir = temp_dir / "cache"
        cache_dir.mkdir()

        # Setup Quicken
        config_file = Path(__file__).parent / "tools.json"
        quicken = Quicken(config_file)
        quicken.cache = QuickenCache(cache_dir)

        results = {}

        # Test MSVC (cl)
        result = test_tool(
            quicken,
            cpp_file,
            "cl",
            ["/c", "/nologo", "/EHsc"],
            [cpp_file.parent / "test.obj"]
        )
        if result:
            results["MSVC (cl)"] = result

        # Test Clang
        result = test_tool(
            quicken,
            cpp_file,
            "clang",
            ["-c"],
            [cpp_file.parent / "test.o"]
        )
        if result:
            results["Clang"] = result

        # Test clang-tidy (analysis only - no output files)
        result = test_tool(
            quicken,
            cpp_file,
            "clang-tidy",
            ["-checks=modernize-*,readability-*"],
            []  # No output files for analysis tool
        )
        if result:
            results["clang-tidy"] = result

        # Display summary
        print("\n" + "="*60)
        print("PERFORMANCE SUMMARY")
        print("="*60)
        print(f"{'Tool':<15} {'Cache MISS':<12} {'Cache HIT':<12} {'Speedup':<10}")
        print("-"*60)

        for tool_name, (miss_time, hit_time, speedup) in results.items():
            print(f"{tool_name:<15} {miss_time:>10.3f}s  {hit_time:>10.3f}s  {speedup:>8.1f}x")

        print("="*60)

        if not results:
            print("\nWARNING: No tools were successfully tested.")
            print("Check that tools are configured in tools.json")


if __name__ == "__main__":
    main()
