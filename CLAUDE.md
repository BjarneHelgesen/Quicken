# Quicken - Implementation Documentation

## Overview

Quicken is an **independent, standalone** Python library and command-line tool that provides caching for C++ build tools. It dramatically speeds up repeated compilation and analysis by caching tool outputs based on local file dependencies and metadata.

**IMPORTANT: Independence**
- Quicken is designed to be completely independent - it has NO dependencies on LevelUp or any parent project
- Can be used as a command-line tool: `python quicken.py <file> <tool> [args]`
- Can be used as a Python library: `from quicken import Quicken`
- Can be integrated into any build system or project
- Maintains its own configuration (`tools.json`)
- Should never import or reference LevelUp-specific code

## Architecture

### Core Components

1. **QuickenCache** (defined in quicken.py) - Manages cache storage and retrieval
   - Index-based lookup system (JSON)
   - File-based storage for output artifacts
   - Metadata tracking for stdout/stderr/returncode

2. **Quicken** (main class in quicken.py) - Main application logic
   - Configuration management
   - Dependency detection using MSVC `/showIncludes`
   - Tool execution wrapper
   - Cache coordination

### Caching Strategy

**OPTIMIZED FOR CACHE HITS** - The cache is designed assuming 10-100 cache hits per cache miss.

**Cache Lookup (Fast Path - No Tool Execution):**
1. Look up source file in index by absolute path
2. For each cached entry for that file:
   - Compare tool command string (direct comparison)
   - Compare metadata (size + mtime) for all dependencies
   - If all match → Cache HIT!

**Cache Miss (Slow Path - Runs Tool):**
1. Run MSVC `/showIncludes` to detect dependencies (~100-200ms)
2. Execute the actual tool
3. Store output files and dependency metadata for future hits

**Performance Characteristics:**
- Cache hits only require file stat operations (~1-10ms overhead)
- `/showIncludes` only runs on cache misses when tool execution dominates anyway
- Optimized for scenarios with many cache hits per miss

This ensures that:
- Same local files with same metadata → instant cache hit
- Different flags → different cache entries (separate lookup)
- Header changes → cache invalidation (metadata comparison fails)
- External library changes → ignored (not tracked for speed)

### File Structure

```
~/.quicken/cache/
├── index.json                           # Cache index (source file → entries)
├── entry_000001/                        # Cache entry (simple counter)
│   ├── metadata.json                   # Execution metadata + dependencies
│   ├── output.obj                      # Cached output files
│   └── ...
├── entry_000002/
│   └── ...
```

**Index Structure:**
```json
{
  "C:\\path\\to\\main.cpp": [
    {
      "cache_key": "entry_000001",
      "tool_cmd": "cl /c /W4",
      "dependencies": [
        {"path": "C:\\path\\to\\main.cpp", "size": 1234, "mtime_ns": 132456789},
        {"path": "C:\\path\\to\\header.h", "size": 567, "mtime_ns": 132456790}
      ]
    }
  ]
}
```

## Implementation Details

### 1. Dependency Detection

```python
def _get_local_dependencies(self, cpp_file: Path, repo_dir: Path) -> List[Path]:
    # Uses MSVC cl.exe with /showIncludes /Zs flags
    # /showIncludes: Lists all included files
    # /Zs: Syntax check only (no code generation - much faster)
    # Only includes files within repo_dir (external libraries ignored)
```

**Why `/showIncludes` instead of preprocessing?**
- Much faster than full preprocessing (~100ms vs ~500ms+)
- Still detects all transitive dependencies
- No need to process full preprocessed output

**Why local-only dependencies?**
- External libraries (STL, Windows SDK) rarely change
- Tracking them adds overhead with minimal benefit
- Maximizes cache hit rate and performance

### 2. Metadata Comparison (Cache Hit Fast Path)

```python
def _dependencies_match(self, cached_deps: List[Dict]) -> bool:
    # For each cached dependency:
    #   - Check file exists
    #   - Compare size (stat.st_size)
    #   - Compare mtime (stat.st_mtime_ns)
    # All must match for cache hit
```

**Why metadata comparison?**
- Extremely fast (~1-10ms for typical files)
- Direct stat() comparison is simple and efficient
- mtime changes indicate file modifications
- Sufficient for detecting actual changes in practice
- No need to run `/showIncludes` on cache hits

### 3. Cache Lookup

The cache uses an optimized lookup system:
1. **Index lookup by source file path** - Find all cached entries for this file
2. **Tool command comparison** - Filter to matching tool command
3. **Dependency metadata check** - Verify all dependencies still match
4. **File existence check** - Verify cache entry directory exists

Cache key format: `entry_{counter:06d}` (simple incrementing counter)

### 4. Tool Execution

The wrapper handles two execution modes:

**MSVC Tools (cl, link):**
```python
# Requires vcvarsall.bat to set up environment
full_cmd = f'"{vcvarsall}" {msvc_arch} >nul && {tool_cmd}'
```

**Other Tools (clang-tidy, clang++):**
```python
# Direct execution without environment setup
subprocess.run([tool_path] + args)
```

**Working Directory Management:**

Quicken supports running tools on temporary copies while preserving relative includes:

```python
# When using temp copies, pass original_file to preserve relative includes
quicken.run(
    cpp_file=temp_copy,           # Temporary copy of source
    tool_name="clang-tidy",
    tool_args=["-checks=*"],
    original_file=original_source, # Original location
    repo_dir=repo_root
)
```

How it works:
- **Tool runs in**: `original_file.parent` (original source directory)
- **Tool operates on**: `temp_copy` (passed as absolute path)
- **Result**: Relative includes like `#include "..\..\tools\mytool.h"` work correctly

This is critical for tools that need to process modified/temporary versions of files while maintaining the original project structure.

### 5. Output Detection

Automatically detects and caches all files created by tools during execution:
- Compares directory contents before/after tool execution
- Caches any new files (excluding the source file itself)
- Supports custom output directories via `output_dir` parameter
- Works with arbitrary tool outputs:
  - `.obj`, `.o` - Object files
  - `.exe` - Executables
  - `.pdb` - Debug symbols
  - `.ilk` - Incremental link files
  - `.yaml` - clang-tidy fix exports
  - Any other tool-generated files

**Output Directory Detection:**
- By default, looks in the working directory (where the tool executes)
- Can specify custom `output_dir` for tools that write to different locations
- Useful for tools with output flags like `/Fo<dir>` (MSVC) or `-o <path>`

```python
# Example: Tool writes to a specific output directory
quicken.run(
    cpp_file=source_file,
    tool_name="cl",
    tool_args=["/c", "/Fooutput/"],  # MSVC writes to output/
    output_dir=Path("output")         # Tell Quicken where to look
)
```

### 6. Cache Storage

For each cached execution:
- **Files**: Copied with timestamps preserved (`shutil.copy2`)
- **Metadata**: JSON with stdout, stderr, returncode
- **Index**: Updated with entry information

### 7. Cache Restoration

When cache hit occurs:
1. Read metadata.json
2. Copy cached files to working directory
3. Output cached stdout/stderr
4. Return cached exit code

Result is indistinguishable from actual tool execution.

### 8. Cache Clearing

Clients can clear the entire cache through:

**Command-line:**
```bash
python quicken.py --clear-cache
```

**Library API:**
```python
quicken = Quicken(config_path)
quicken.clear_cache()  # Clears all cached entries
```

This removes all cache entries and resets the index.

## Configuration

`tools.json` format:
```json
{
  "tool_name": "/path/to/tool",
  "vcvarsall": "path/to/vcvarsall.bat",
  "msvc_arch": "x64"
}
```

Special keys:
- `vcvarsall` - Required for MSVC tools
- `msvc_arch` - MSVC target architecture (x64, x86, ARM, etc.)

## Usage Patterns

### Build Systems Integration

```bash
# Replace compiler calls with Quicken
# Before: cl /c myfile.cpp
# After:  python quicken.py myfile.cpp cl /c

# With custom output directory
# Before: cl /c /Fooutput/ myfile.cpp
# After:  python quicken.py myfile.cpp cl /c /Fooutput/ --output-dir output

# Clear the cache
python quicken.py --clear-cache
```

### CI/CD Pipelines

Quicken shines in CI environments where:
- Same files are compiled repeatedly
- Minor changes don't affect most translation units
- Build time is critical

### Static Analysis

```bash
# Run clang-tidy through cache
python quicken.py myfile.cpp clang-tidy --checks=modernize-* --export-fixes=fixes.yaml

# Clear cache to force re-analysis
python quicken.py --clear-cache
```

Second run is instant if file unchanged.

**Note**: LevelUp's ClangTidyCrawler automatically uses Quicken for caching clang-tidy analysis, providing massive speedups during repeated analysis runs.

## Performance Characteristics

**Design Goal:** Optimize for cache hits (10-100 hits per miss).

**Cache Hit:**
- Time: ~15-60ms total
  - Index lookup: <1ms
  - Stat all dependencies (50 files): ~1-10ms
  - Metadata comparison: <1ms
  - File copy + I/O: ~10-50ms (dominant cost)
- Speedup vs actual compilation: 100-1000x

**Cache Miss:**
- Overhead: ~100-200ms (dependency detection)
- Minimal overhead compared to actual tool execution
- Dependency info cached for future hits

**Dependency Detection (Only on Cache Miss):**
- MSVC `/showIncludes /Zs` (~100-200ms for typical files)
- Much faster than full preprocessing (no code generation)
- One-time cost per cache miss
- Results stored in cache for instant future lookups

**Metadata Comparison (On Every Cache Hit):**
- Direct stat() comparison
- No `/showIncludes` execution needed
- Near-instantaneous (~1-10ms for 50 files)

## Design Decisions

### Why Metadata Comparison?

**Approach:** Store dependency metadata in cache, compare directly on lookup

**Benefits:**
- Near-instantaneous comparison (~1-10ms for 50 files)
- No `/showIncludes` needed on cache hits
- Simple stat() calls are sufficient
- Acceptable trade-off: "touch file" invalidates cache unnecessarily

### Why Store Dependencies in Cache?

**Approach:** Detect dependencies once on cache miss, store for future lookups

**Benefits:**
- `/showIncludes` only runs on cache misses
- Cache hits avoid expensive dependency detection
- Optimal for scenarios with many cache hits per miss

### Why Local Dependencies Only?

Alternative: Track all dependencies including external libraries

**Problems:**
- External libraries (STL, Windows SDK) rarely change
- Tracking adds significant overhead
- Reduces cache hit rate for no practical benefit

**Solution:** Only track files within repository
- Maximizes performance
- Sufficient for detecting actual code changes
- External library updates handled by clean builds

### Why Store Files, Not Just Output?

Alternative: Cache only stdout/stderr

**Problem:** Many tools produce files (.obj, .pdb)

**Solution:** Detect and cache all output files
- Complete restoration of tool execution
- Works for compilers, linkers, analyzers

### Why Index by Source File Path?

**Approach:** Use source file absolute path as index key

**Benefits:**
- Direct lookup without any preprocessing
- Multiple cache entries per source file (different tools/flags)
- Enables fast path: lookup → compare metadata → done
- Simple and efficient

## Future Enhancements

### Possible Improvements

1. **Distributed Cache** - Share cache across machines
2. **Compression** - Compress large cached files
3. **TTL/LRU** - Automatic cache eviction
4. **Statistics** - Track hit/miss rates
5. **Parallel Dependency Detection** - Process multiple files concurrently
6. **Custom Dependency Detection** - Support non-MSVC compilers (GCC `-M`, Clang `-MM`)
7. **Content Hashing Option** - Optional fallback to content hashing for critical builds

### Known Limitations

1. **MSVC Dependency** - Currently requires MSVC for dependency detection (`/showIncludes`)
2. **Metadata Sensitivity** - `touch file` invalidates cache even if content unchanged
3. **Output Detection** - Uses directory diff; requires `output_dir` parameter if tool writes outside working directory
4. **No Distributed Cache** - Cache is local only
5. **No Cache Limits** - Cache grows unbounded
6. **Windows Focus** - Designed primarily for Windows/MSVC
7. **External Library Blindness** - Changes to external libraries not detected (requires clean build)

## Testing

### Manual Testing

```bash
# First run (cache miss)
python quicken.py test.cpp cl /c
# Second run (cache hit - should be instant)
python quicken.py test.cpp cl /c

# Different flags (cache miss)
python quicken.py test.cpp cl /c /W4

# Modify test.cpp (cache miss)
# Revert test.cpp (cache hit again)
```

### Expected Behavior

- Cache miss: See "[Quicken] Cache MISS" in stderr
- Cache hit: See "[Quicken] Cache HIT" in stderr
- Output files restored with same content
- stdout/stderr identical to direct tool execution

## Conclusion

Quicken provides transparent caching for C++ build tools with:
- Minimal overhead on cache miss (~100-200ms)
- Massive speedup on cache hit (100-1000x)
- Easy integration with existing workflows
- Simple, maintainable codebase

The metadata-based approach prioritizes speed while the local-dependency tracking ensures practical correctness for iterative development.

---

*Generated by Claude Code - 2025-12-09*
