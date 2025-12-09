#!/usr/bin/env python3
"""
Example: Using Quicken as a library instead of subprocess

This demonstrates the performance difference between calling Quicken
as a subprocess vs using it as a library.
"""

from pathlib import Path
from quicken import Quicken
import time

def example_subprocess_usage():
    """OLD WAY: High overhead (40-100ms per call from Python startup)"""
    import subprocess

    files = ["test.cpp"] * 10  # Simulate 10 files

    start = time.perf_counter()
    for cpp_file in files:
        subprocess.run(
            ["python", "quicken.py", cpp_file, "cl", "/c"],
            capture_output=True
        )
    elapsed = time.perf_counter() - start

    print(f"Subprocess method: {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")


def example_library_usage_verbose():
    """NEW WAY: Low overhead with verbose output (for debugging)"""

    # Initialize once
    quicken = Quicken(Path("tools.json"), verbose=True)

    files = ["test.cpp"] * 10

    start = time.perf_counter()
    for cpp_file in files:
        returncode = quicken.run(
            cpp_file=Path(cpp_file),
            tool_name="cl",
            tool_args=["/c"]
        )
    elapsed = time.perf_counter() - start

    print(f"\nLibrary method (verbose): {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")


def example_library_usage_quiet():
    """NEW WAY: Low overhead with quiet mode (for production)"""

    # Initialize once with verbose=False
    quicken = Quicken(Path("tools.json"), verbose=False)

    files = ["test.cpp"] * 100  # Simulate 100 files

    print("\n[Running 100 files in quiet mode...]")
    start = time.perf_counter()
    for cpp_file in files:
        returncode = quicken.run(
            cpp_file=Path(cpp_file),
            tool_name="cl",
            tool_args=["/c"]
        )
        if returncode != 0:
            print(f"ERROR: {cpp_file} failed with code {returncode}")
    elapsed = time.perf_counter() - start

    print(f"\nLibrary method (quiet): {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")
    print(f"  Estimated time for 1000 files: {elapsed*10:.1f}s")


def example_parallel_builds():
    """Advanced: Process multiple files with progress tracking"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    quicken = Quicken(Path("tools.json"), verbose=False)

    # Simulate a larger project
    files = ["test.cpp"] * 500

    def compile_file(cpp_file):
        """Compile a single file"""
        returncode = quicken.run(
            cpp_file=Path(cpp_file),
            tool_name="cl",
            tool_args=["/c", "/W4"]
        )
        return cpp_file, returncode

    print("\n[Parallel compilation of 500 files...]")
    start = time.perf_counter()

    # Use thread pool for parallel execution
    # Note: Due to GIL, actual parallelism depends on I/O
    # For CPU-bound preprocessing, consider multiprocessing
    with ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(compile_file, f) for f in files]

        completed = 0
        failed = []
        for future in as_completed(futures):
            cpp_file, returncode = future.result()
            completed += 1
            if returncode != 0:
                failed.append(cpp_file)

            # Show progress every 50 files
            if completed % 50 == 0:
                print(f"  Progress: {completed}/{len(files)} files")

    elapsed = time.perf_counter() - start

    print(f"\nParallel compilation: {elapsed:.2f}s for {len(files)} files")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per file")
    if failed:
        print(f"  Failed: {len(failed)} files")


if __name__ == "__main__":
    print("=" * 60)
    print("Quicken Library Usage Examples")
    print("=" * 60)

    # Uncomment to compare subprocess vs library:
    # example_subprocess_usage()

    example_library_usage_verbose()
    example_library_usage_quiet()

    # Uncomment for parallel build example:
    # example_parallel_builds()

    print("\n" + "=" * 60)
    print("Summary:")
    print("  - Use verbose=True for debugging/CLI usage")
    print("  - Use verbose=False for production/library usage")
    print("  - Initialize Quicken once, call run() many times")
    print("  - Expected speedup: 40-100x vs subprocess approach")
    print("=" * 60)
