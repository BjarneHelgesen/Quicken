#!/usr/bin/env python3
"""
Deep profiling of cache hit happy paths to identify optimization opportunities.

Happy Path 1: mtime/size match (no hashing)
Happy Path 2: mtime changed but hash matches (hashing required)
"""

import cProfile
import pstats
import shutil
import tempfile
import time
from pathlib import Path
from io import StringIO

from quicken import Quicken
from quicken._cache import QuickenCache


def create_simple_project(temp_dir: Path, num_headers: int = 10):
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


def analyze_profiler_detailed(profiler):
    """Extract detailed timing breakdown from profiler."""
    stats = pstats.Stats(profiler)
    stats.strip_dirs()

    # Capture full stats
    stream = StringIO()
    ps = pstats.Stats(profiler, stream=stream)
    ps.strip_dirs()
    ps.sort_stats('cumulative')
    ps.print_stats()

    # Parse for key operations
    breakdown = {
        'total': 0,
        'lookup': 0,
        'lookup_index': 0,
        'dependencies_match': 0,
        'calculateHash': 0,
        'file_stat': 0,
        'restore': 0,
        'restore_json_load': 0,
        'restore_copy': 0,
        'restore_submit': 0,
        'restore_wait': 0,
        'path_operations': 0,
        'toAbsolutePath': 0,
        'fromString': 0,
    }

    for key, stat in stats.stats.items():
        func_name = key[2]
        tottime = stat[2]
        cumtime = stat[3]
        ncalls = stat[0]

        breakdown['total'] = max(breakdown['total'], cumtime)

        # Lookup operations
        if func_name == 'lookup':
            breakdown['lookup'] = cumtime
        elif func_name == '__contains__' or func_name == 'get':
            breakdown['lookup_index'] += tottime

        # Dependency checking
        elif func_name == '_dependencies_match':
            breakdown['dependencies_match'] = cumtime
        elif func_name == 'calculateHash':
            breakdown['calculateHash'] += tottime
        elif func_name == 'stat' or 'stat' in func_name.lower():
            breakdown['file_stat'] += tottime

        # Restore operations
        elif func_name == 'restore':
            breakdown['restore'] = cumtime
        elif func_name == 'load' and 'json' in str(key[0]).lower():
            breakdown['restore_json_load'] += tottime
        elif func_name == 'submit':
            breakdown['restore_submit'] += tottime
        elif func_name == 'result':
            breakdown['restore_wait'] += tottime
        elif func_name == '_copy_file':
            breakdown['restore_copy'] += cumtime
        elif func_name == 'copyfile' or func_name == 'copy2':
            breakdown['restore_copy'] += tottime

        # Path operations
        elif func_name == 'toAbsolutePath':
            breakdown['path_operations'] += tottime
            breakdown['toAbsolutePath'] += tottime
        elif func_name == 'fromString':
            breakdown['fromString'] += tottime
        elif func_name == 'resolve' or func_name == 'realpath':
            breakdown['path_operations'] += tottime

    return breakdown


def profile_happy_path(scenario_name: str, num_headers: int, touch_files: bool):
    """Profile a cache hit scenario with detailed breakdown."""

    with tempfile.TemporaryDirectory() as tmpdir:
        temp_dir = Path(tmpdir)
        main_cpp, headers = create_simple_project(temp_dir, num_headers)

        cache_dir = temp_dir / "cache"
        cache_dir.mkdir()

        # Copy tools.json to ~/.quicken/tools.json
        config_file = Path(__file__).parent / "tools.json"
        quicken_dir = Path.home() / ".quicken"
        quicken_dir.mkdir(parents=True, exist_ok=True)
        shutil.copy(config_file, quicken_dir / "tools.json")

        quicken = Quicken(temp_dir)
        quicken.cache = QuickenCache(cache_dir)

        # Populate cache
        quicken.run(main_cpp, "cl", ["/c", "/nologo", "/EHsc"], [], [])

        obj_file = main_cpp.parent / "main.obj"
        obj_file.unlink()

        # Touch files if requested (forces hash fallback)
        if touch_files:
            time.sleep(0.01)
            for f in [main_cpp] + headers:
                f.write_text(f.read_text())

        # Profile cache hit
        profiler = cProfile.Profile()
        profiler.enable()

        quicken.run(main_cpp, "cl", ["/c", "/nologo", "/EHsc"], [], [])

        profiler.disable()


        return analyze_profiler_detailed(profiler)


def main():
    print("=" * 90)
    print("HAPPY PATH PROFILING - DETAILED BREAKDOWN")
    print("=" * 90)

    num_deps = 20

    # Happy Path 1: mtime/size match (fastest)
    print(f"\nProfiling Happy Path 1: mtime/size match ({num_deps} deps)...")
    result1 = profile_happy_path("mtime_match", num_deps, touch_files=False)

    # Happy Path 2: hash match (after hash calculation)
    print(f"Profiling Happy Path 2: hash match after touch ({num_deps} deps)...")
    result2 = profile_happy_path("hash_match", num_deps, touch_files=True)

    # Display results
    print("\n" + "=" * 90)
    print("DETAILED BREAKDOWN (all times in milliseconds)")
    print("=" * 90)

    categories = [
        ('Total Time', 'total'),
        ('  Lookup', 'lookup'),
        ('    Index access', 'lookup_index'),
        ('    Dep validation', 'dependencies_match'),
        ('      File stat', 'file_stat'),
        ('      Hash calc', 'calculateHash'),
        ('  Restore', 'restore'),
        ('    Submit to pool', 'restore_submit'),
        ('    Wait for copy', 'restore_wait'),
        ('    Copy files', 'restore_copy'),
        ('    JSON load', 'restore_json_load'),
        ('Path operations', 'path_operations'),
        ('  toAbsolutePath', 'toAbsolutePath'),
        ('  fromString', 'fromString'),
    ]

    print(f"\n{'Operation':<35} {'Path 1 (mtime)':<20} {'Path 2 (hash)':<20}")
    print("-" * 90)

    for label, key in categories:
        val1 = result1.get(key, 0) * 1000
        val2 = result2.get(key, 0) * 1000
        pct1 = (result1.get(key, 0) / result1['total'] * 100) if result1['total'] > 0 else 0
        pct2 = (result2.get(key, 0) / result2['total'] * 100) if result2['total'] > 0 else 0

        print(f"{label:<35} {val1:>8.3f}ms ({pct1:>4.1f}%)  {val2:>8.3f}ms ({pct2:>4.1f}%)")

    # Analysis
    print("\n" + "=" * 90)
    print("OPTIMIZATION OPPORTUNITIES")
    print("=" * 90)

    total1 = result1['total'] * 1000
    total2 = result2['total'] * 1000

    print(f"\nTotal cache hit time:")
    print(f"  Happy Path 1 (mtime match): {total1:.3f}ms")
    print(f"  Happy Path 2 (hash match):  {total2:.3f}ms")
    print(f"  Difference: {total2-total1:.3f}ms ({(total2-total1)/total1*100:.1f}% slower)")

    # Identify top bottlenecks for each path
    print("\n" + "-" * 90)
    print("Top bottlenecks in Happy Path 1 (mtime match):")
    print("-" * 90)

    bottlenecks1 = []
    for label, key in categories:
        # Skip indented items and total
        if not label.startswith('  ') and key != 'total':
            pct = (result1.get(key, 0) / result1['total'] * 100) if result1['total'] > 0 else 0
            if pct > 5:  # Only show >5%
                bottlenecks1.append((label, result1.get(key, 0) * 1000, pct))

    for label, ms, pct in sorted(bottlenecks1, key=lambda x: x[2], reverse=True):
        print(f"  {label:<30}: {ms:>8.3f}ms ({pct:>4.1f}%)")

        # Specific optimization suggestions
        if 'Restore' in label and pct > 40:
            print(f"    -> File copying dominates. Consider:")
            print(f"       • Use hardlinks instead of copy (if same filesystem)")
            print(f"       • Optimize shutil.copy2 usage")
            print(f"       • Parallelize multi-file restoration")
        elif 'Path operations' in label and pct > 10:
            print(f"    -> Path resolution overhead. Consider:")
            print(f"       • Cache resolved paths")
            print(f"       • Use string operations instead of Path.resolve()")
        elif 'Lookup' in label and pct > 10:
            print(f"    -> Index lookup overhead. Consider:")
            print(f"       • Use dict instead of list for index entries")
            print(f"       • Cache lookup results")

    print("\n" + "-" * 90)
    print("Additional overhead in Happy Path 2 (hash match):")
    print("-" * 90)

    extra_hash = (result2['calculateHash'] - result1['calculateHash']) * 1000
    extra_total = total2 - total1

    print(f"  Hash calculation: {extra_hash:.3f}ms")
    print(f"  Other overhead:   {extra_total - extra_hash:.3f}ms")
    print(f"  Total overhead:   {extra_total:.3f}ms")

    if extra_hash > 0:
        files_hashed = num_deps + 1  # all headers + main.cpp
        print(f"\n  Average hash time per file: {extra_hash/files_hashed:.3f}ms")
        print(f"  Files hashed: ~{files_hashed}")

    # Final recommendations
    print("\n" + "=" * 90)
    print("RECOMMENDED OPTIMIZATIONS (in priority order)")
    print("=" * 90)

    recommendations = []

    # Check restore time
    restore_pct1 = (result1['restore'] / result1['total'] * 100) if result1['total'] > 0 else 0
    if restore_pct1 > 40:
        recommendations.append((
            restore_pct1,
            "1. OPTIMIZE FILE RESTORATION",
            "   Current: Uses shutil.copy2() for each file",
            "   Suggestion: Use hardlinks (os.link) when source and dest on same filesystem",
            f"   Impact: Could reduce restore from {result1['restore']*1000:.2f}ms to ~1ms"
        ))

    # Check path operations
    path_pct1 = (result1['path_operations'] / result1['total'] * 100) if result1['total'] > 0 else 0
    if path_pct1 > 10:
        recommendations.append((
            path_pct1,
            "2. OPTIMIZE PATH RESOLUTION",
            "   Current: Calls Path.resolve() frequently",
            "   Suggestion: Cache repo_dir.resolve() at init, use simple joins",
            f"   Impact: Could reduce path ops from {result1['path_operations']*1000:.2f}ms to <1ms"
        ))

    # Check lookup overhead
    lookup_pct1 = (result1['lookup'] / result1['total'] * 100) if result1['total'] > 0 else 0
    if lookup_pct1 > 15:
        recommendations.append((
            lookup_pct1,
            "3. OPTIMIZE INDEX LOOKUP",
            "   Current: Linear search through list of cache entries",
            "   Suggestion: Use dict keyed by (tool_name, tool_args_hash)",
            f"   Impact: Could reduce lookup from {result1['lookup']*1000:.2f}ms to <1ms"
        ))

    # Print recommendations in order of impact
    for _, *lines in sorted(recommendations, key=lambda x: x[0], reverse=True):
        for line in lines:
            print(line)
        print()

    print("=" * 90)


if __name__ == "__main__":
    main()
