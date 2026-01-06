#!/usr/bin/env python3
"""
Integration test demonstrating duplicate cache entry bug.

BUG: Quicken creates separate cache entries for identical file content
when compiled from different directories in the same run.

Expected: Reuse existing cache entry when content hash matches
Actual: Create new cache entry and overwrite index pointer
"""

import tempfile
from pathlib import Path
import pytest

from quicken import Quicken, QuickenCache


def test_duplicate_cache_entries_for_same_content(config_file, temp_dir):
    """Test that Quicken creates duplicate entries for same content in different dirs.

    This test demonstrates a performance issue where Quicken creates redundant
    cache entries instead of detecting that the content hash already exists.

    Steps:
    1. Compile file with content A from directory 1 → creates entry_000001
    2. Compile file with content A from directory 2 → creates entry_000002 (DUPLICATE)
    3. Both entries have identical content hash

    Expected: Step 2 should reuse entry_000001
    Actual: Step 2 creates a new entry_000002
    """

    # Create cache directory
    cache_dir = temp_dir / "cache"
    cache_dir.mkdir()

    # Create two separate directories with IDENTICAL source files
    dir1 = temp_dir / "compile_dir_1"
    dir2 = temp_dir / "compile_dir_2"
    dir1.mkdir()
    dir2.mkdir()

    # Identical source code in both directories
    source_code = """
int add(int a, int b) {
    return a + b;
}
"""

    file1 = dir1 / "test.cpp"
    file2 = dir2 / "test.cpp"
    file1.write_text(source_code)
    file2.write_text(source_code)

    # Compilation parameters
    tool_args = ["-std=c++20", "-Wall", "-S", "-masm=intel"]

    # First compilation from dir1 - create Quicken instance for dir1
    quicken1 = Quicken(config_file, dir1, cache_dir=cache_dir)
    initial_cache_count = len(quicken1.cache.index)

    returncode1 = quicken1.run(
        file1.relative_to(dir1),
        "clang++",
        tool_args,
        output_args=["-o", str(dir1 / "test.s")],
        optimization=0
    )

    if returncode1 != 0:
        pytest.skip("Clang++ compilation failed")

    # Check that one cache entry was created
    cache_count_after_first = len(quicken1.cache.index)
    assert cache_count_after_first == initial_cache_count + 1, \
        "First compilation should create exactly one cache entry"

    # Get the cache entry created by first compilation
    # Index now stores lists of entries, so we need to flatten
    cache_entries = []
    for entries_list in quicken1.cache.index.values():
        cache_entries.extend(entries_list)
    first_entry_key = cache_entries[-1]['cache_key']

    # Read metadata to get content hash
    first_metadata_file = cache_dir / first_entry_key / "metadata.json"
    import json
    with open(first_metadata_file) as f:
        first_metadata = json.load(f)
    first_content_hash = first_metadata['dependencies'][0]['hash']

    # Second compilation from dir2 with IDENTICAL content - create Quicken instance for dir2
    quicken2 = Quicken(config_file, dir2, cache_dir=cache_dir)

    returncode2 = quicken2.run(
        file2.relative_to(dir2),
        "clang++",
        tool_args,
        output_args=["-o", str(dir2 / "test.s")],
        optimization=0
    )

    assert returncode2 == 0, "Second compilation should succeed"

    # Check cache entries after second compilation
    cache_count_after_second = len(quicken2.cache.index)

    # Get all cache entry directories
    cache_entry_dirs = sorted([d for d in cache_dir.iterdir() if d.name.startswith("entry_")])

    # Read metadata from all entries
    all_hashes = []
    for entry_dir in cache_entry_dirs:
        metadata_file = entry_dir / "metadata.json"
        if metadata_file.exists():
            with open(metadata_file) as f:
                metadata = json.load(f)
                content_hash = metadata['dependencies'][0]['hash']
                all_hashes.append((entry_dir.name, content_hash))

    # Find duplicate hashes
    hash_counts = {}
    for entry_name, content_hash in all_hashes:
        if content_hash not in hash_counts:
            hash_counts[content_hash] = []
        hash_counts[content_hash].append(entry_name)

    duplicates = {h: entries for h, entries in hash_counts.items() if len(entries) > 1}

    # Report the bug
    if duplicates:
        duplicate_report = "\n".join(
            f"  Hash {h}: {len(entries)} entries {entries}"
            for h, entries in duplicates.items()
        )
        pytest.fail(
            f"BUG DETECTED: Quicken created duplicate cache entries for identical content:\n"
            f"{duplicate_report}\n\n"
            f"Expected: Second compilation should reuse existing entry with matching hash\n"
            f"Actual: Created new entry instead of reusing existing one\n\n"
            f"Impact: Wastes disk space and creates orphaned cache entries"
        )

    # If we get here, the bug is fixed
    print(f"\nSUCCESS: No duplicate cache entries found")
    print(f"Total cache entries: {len(cache_entry_dirs)}")
    print(f"Unique content hashes: {len(hash_counts)}")


def test_duplicate_entries_within_single_test_run(config_file, temp_dir):
    """Test that demonstrates multiple duplicate entries created in one run.

    Simulates what happens during pytest run where the same test.cpp
    gets compiled multiple times with identical content but from different
    temporary directories.
    """

    cache_dir = temp_dir / "cache"
    cache_dir.mkdir()

    # Identical source code
    source_code = """
int multiply(int x, int y) {
    return x * y;
}
"""

    # Compile the same content from 5 different directories
    num_compilations = 5
    for i in range(num_compilations):
        compile_dir = temp_dir / f"dir_{i}"
        compile_dir.mkdir()

        source_file = compile_dir / "test.cpp"
        source_file.write_text(source_code)

        # Create a new Quicken instance for each directory
        quicken = Quicken(config_file, compile_dir, cache_dir=cache_dir)

        returncode = quicken.run(
            source_file.relative_to(compile_dir),
            "clang++",
            ["-std=c++20", "-Wall", "-S", "-masm=intel"],
            output_args=["-o", str(compile_dir / "test.s")],
            optimization=0
        )

        if returncode != 0:
            pytest.skip(f"Compilation {i} failed")

    # Count cache entries
    cache_entry_dirs = [d for d in cache_dir.iterdir() if d.name.startswith("entry_")]

    if len(cache_entry_dirs) > 1:
        pytest.fail(
            f"BUG: Created {len(cache_entry_dirs)} cache entries for identical content "
            f"(expected 1)\n"
            f"Each compilation created a duplicate entry instead of reusing the existing one"
        )


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
