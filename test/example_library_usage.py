#!/usr/bin/env python3
"""
Example of using Quicken 
"""

from pathlib import Path
from quicken import Quicken
import time

def example_subprocess_usage():
    import subprocess

    files = ["test.cpp"] * 10  # Simulate 10 files

    start = time.perf_counter()
    for source_file in files:
        subprocess.run(
            ["python", "quicken.py", source_file, "cl", "/c"],
            capture_output=True
        )
    elapsed = time.perf_counter() - start

    print(f"Subprocess method: {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")


def example_library_usage_verbose():
    # Initialize once with the repository directory
    # Tools must be pre-configured in ~/.quicken/tools.json
    quicken = Quicken(Path.cwd())

    files = ["test.cpp"] * 10

    start = time.perf_counter()
    for source_file in files:
        # Using new convenience method (recommended)
        returncode = quicken.cl(
            source_file=Path(source_file),
            tool_args=["/c"],
            output_args=[],
            input_args=[]
        )
    elapsed = time.perf_counter() - start

    print(f"\nLibrary method (verbose, using cl() convenience method): {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")


def example_library_usage_quiet():
    """Quiet mode (for production)"""

    # Initialize once with the repository directory
    quicken = Quicken(Path.cwd())

    files = ["test.cpp"] * 100  # Simulate 100 files

    print("\n[Running 100 files in quiet mode...]")
    start = time.perf_counter()
    for source_file in files:
        # Using convenience method
        returncode = quicken.cl(
            source_file=Path(source_file),
            tool_args=["/c"],
            output_args=[],
            input_args=[]
        )
        if returncode != 0:
            print(f"ERROR: {source_file} failed with code {returncode}")
    elapsed = time.perf_counter() - start

    print(f"\nLibrary method (quiet, using cl() convenience method): {elapsed:.2f}s for {len(files)} calls")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per call")
    print(f"  Estimated time for 1000 files: {elapsed*10:.1f}s")


def example_parallel_builds():
    """Advanced: Process multiple files with progress tracking"""
    from concurrent.futures import ThreadPoolExecutor, as_completed

    quicken = Quicken(Path.cwd())

    # Simulate a larger project
    files = ["test.cpp"] * 500

    def compile_file(source_file):
        """Compile a single file"""
        # Using convenience method
        returncode = quicken.cl(
            source_file=Path(source_file),
            tool_args=["/c", "/W4"],
            output_args=[],
            input_args=[]
        )
        return source_file, returncode

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
            source_file, returncode = future.result()
            completed += 1
            if returncode != 0:
                failed.append(source_file)

            # Show progress every 50 files
            if completed % 50 == 0:
                print(f"  Progress: {completed}/{len(files)} files")

    elapsed = time.perf_counter() - start

    print(f"\nParallel compilation: {elapsed:.2f}s for {len(files)} files")
    print(f"  ~{elapsed/len(files)*1000:.1f}ms per file")
    if failed:
        print(f"  Failed: {len(failed)} files")


if __name__ == "__main__":
    print("Quicken Library Usage Examples")

    # Uncomment to compare subprocess vs library:
    # example_subprocess_usage()

    example_library_usage_verbose()
    example_library_usage_quiet()

    # Uncomment for parallel build example:
    # example_parallel_builds()

    print("\n" + "=" * 60)
    print("Summary:")
    print("  - Tools must be pre-configured in ~/.quicken/tools.json (by installer)")
    print("  - Initialize Quicken once with repo_dir, use convenience methods many times")
    print("  - Convenience methods: quicken.cl(), quicken.clang(), quicken.clang_tidy(), quicken.doxygen()")
    print("  - Generic method available: quicken.run() for flexibility")
    print("  - Use output_args for output-specific flags (not part of cache key)")
    print("=" * 60)
