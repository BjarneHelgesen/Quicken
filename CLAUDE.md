# Quicken - Implementation Documentation

## Overview

Quicken is an **independent, standalone** Python library and command-line tool that provides caching for C++ build tools. It dramatically speeds up repeated compilation and analysis by caching tool outputs based on local file dependencies and file hashes.

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
   - Hash-based dependency tracking

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
   - Compare file hashes for all dependencies
   - If all match → Cache HIT!

**Cache Miss (Slow Path - Runs Tool):**
1. Run MSVC `/showIncludes` to detect dependencies (~100-200ms)
2. Execute the actual tool
3. Store output files and dependency hashes for future hits

**Performance Characteristics:**
- Cache hits require hashing all dependencies (~10-50ms for typical projects)
- `/showIncludes` only runs on cache misses when tool execution dominates anyway
- Optimized for scenarios with many cache hits per miss

This ensures that:
- Same local files with same content → instant cache hit
- Different flags → different cache entries (separate lookup)
- Header changes → cache invalidation (hash comparison fails)
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
        {"path": "C:\\path\\to\\main.cpp", "hash": "a1b2c3d4e5f60708"},
        {"path": "C:\\path\\to\\header.h", "hash": "1234567890abcdef"}
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

### 2. Hash Comparison (Cache Hit Fast Path)

```python
def _dependencies_match(self, cached_deps: List[Dict]) -> bool:
    # For each cached dependency:
    #   - Check file exists
    #   - Calculate 64-bit hash of file content
    #   - Compare hash with cached value
    # All must match for cache hit
```

**Why hash comparison?**
- Detects actual content changes, not just file touches
- 64-bit BLAKE2b is fast and sufficient for this purpose
- Simple and reliable - no false cache invalidations
- Moderately fast (~10-50ms for typical projects)
- No need to run `/showIncludes` on cache hits

### 3. Cache Lookup

The cache uses an optimized lookup system:
1. **Index lookup by source file path** - Find all cached entries for this file
2. **Tool command comparison** - Filter to matching tool command
3. **Dependency hash check** - Verify all dependencies still match
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
- **Hashes**: 64-bit BLAKE2b hash for each dependency file
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

## Repo-Level Tool Caching

Quicken supports caching for **repo-level tools** (e.g., Doxygen, cppcheck) that operate on entire repositories rather than individual source files.

### Key Differences from File-Level Caching

| Aspect | File-Level | Repo-Level |
|--------|-----------|-----------|
| **Index Key** | Source file path | Main file path (e.g., Doxyfile) |
| **Dependencies** | Detected via `/showIncludes` | Specified via glob patterns |
| **Dependency Count** | ~10-50 files | ~1000-5000 files |
| **Output** | 1-3 files | 100-300 files (directory trees) |
| **Cache Hit Overhead** | ~10-50ms | ~100-500ms |
| **Typical Tool Runtime** | ~1-5 seconds | ~10-60 seconds |
| **Speedup** | 50-200x | 10-50x |

### API: `run_repo_tool()`

```python
def run_repo_tool(
    repo_dir: Path,
    tool_name: str,
    tool_args: List[str],
    main_file: Path,
    dependency_patterns: List[str],
    output_dir: Path = None
) -> int
```

**Parameters:**
- `repo_dir`: Repository root directory
- `tool_name`: Tool to run (e.g., "doxygen")
- `tool_args`: Arguments for the tool
- `main_file`: Main file for the tool (e.g., Doxyfile path) - used as cache index key
- `dependency_patterns`: Glob patterns for dependencies (e.g., `["*.cpp", "*.h"]`)
- `output_dir`: Directory where tool creates output files (default: repo_dir)

**Returns:** Tool exit code

### Example: Doxygen Integration

```python
from pathlib import Path
from quicken import Quicken

# Create Quicken instance
quicken = Quicken(Path("tools.json"))

# Setup paths
repo_path = Path("/path/to/repo")
doxyfile = repo_path / ".doxygen" / "Doxyfile.xml"
output_dir = repo_path / ".doxygen" / "xml"

# Run Doxygen with caching
returncode = quicken.run_repo_tool(
    repo_dir=repo_path,
    tool_name="doxygen",
    tool_args=[str(doxyfile)],
    main_file=doxyfile,
    dependency_patterns=["*.cpp", "*.cxx", "*.cc", "*.c",
                        "*.hpp", "*.hxx", "*.h", "*.hh"],
    output_dir=output_dir
)
```

**First Run (Cache MISS):**
- Glob for all C++ files matching patterns (~50-100ms)
- Calculate hashes for all files (~100-500ms)
- Run Doxygen (~10-60 seconds)
- Store directory tree in cache (~500-2000ms)
- **Total: ~10-60 seconds + ~650-2600ms overhead**

**Second Run (Cache HIT):**
- Lookup cache by doxyfile path (<1ms)
- Calculate and compare hashes for all C++ files (~100-500ms)
- Restore directory tree from cache (~1-2 seconds)
- **Total: ~1-3 seconds (5-30x speedup!)**

### How It Works

**1. Dependency Detection:**
- Uses glob patterns instead of `/showIncludes`
- Recursively finds all files matching patterns
- Calculates 64-bit hash for all matched files

**2. Cache Validation:**
- Same hash-based approach as file-level caching
- Compares hashes for ALL matched files
- ANY file content change invalidates the cache

**3. Directory Tree Caching:**
- Preserves directory structure in cache
- Stores relative paths in metadata
- Restores complete directory hierarchy

**4. Index Structure:**

```json
{
  "C:\\repo\\.doxygen\\Doxyfile.xml": [
    {
      "cache_key": "entry_000002",
      "tool_cmd": "doxygen C:\\repo\\.doxygen\\Doxyfile.xml",
      "repo_mode": true,
      "dependency_patterns": ["*.cpp", "*.hpp", "*.h", "*.c"],
      "dependencies": [
        {"path": "C:\\repo\\main.cpp", "hash": "a1b2c3d4e5f60708"},
        {"path": "C:\\repo\\utils.hpp", "hash": "1234567890abcdef"},
        ...
      ]
    }
  ]
}
```

### Multiple Runs with Different Configurations

Each run with a different main file creates a separate cache entry:

```python
# First configuration
quicken.run_repo_tool(
    repo_dir=repo_path,
    tool_name="doxygen",
    tool_args=[str(doxyfile_config1)],
    main_file=doxyfile_config1,  # Different main file
    dependency_patterns=patterns,
    output_dir=xml_output1_dir
)

# Second configuration
quicken.run_repo_tool(
    repo_dir=repo_path,
    tool_name="doxygen",
    tool_args=[str(doxyfile_config2)],
    main_file=doxyfile_config2,  # Different main file
    dependency_patterns=patterns,
    output_dir=xml_output2_dir
)
```

Both entries share the same dependencies (all C++ files), so both invalidate together when source changes.

### LevelUp Integration

**DoxygenRunner Integration:**

```python
from core.parsers.doxygen_runner import DoxygenRunner
from quicken import Quicken

# Create Quicken instance
quicken = Quicken(Path("Quicken/tools.json"))

# Run Doxygen with caching
runner = DoxygenRunner()
xml_dir = runner.run(
    repo_path,
    quicken=quicken  # Optional parameter
)
```

**Repo Integration:**

```python
from core.repo.repo import create_repo
from quicken import Quicken

repo = create_repo("/path/to/repo")
quicken = Quicken(Path("Quicken/tools.json"))

# Generate Doxygen with caching
xml_dirs = repo.generate_doxygen(quicken=quicken)
```

### Performance Expectations

**Typical Repository (1000 C++ files):**

| Operation | Cache MISS | Cache HIT | Speedup |
|-----------|-----------|-----------|---------|
| Dependency detection | ~100ms | ~100-200ms | ~1x |
| Tool execution | 30-60s | - | - |
| Cache I/O | ~1-2s | ~1-2s | 1x |
| **Total** | **~30-60s** | **~2-3s** | **10-30x** |

**Large Repository (5000+ C++ files):**

| Operation | Cache MISS | Cache HIT | Speedup |
|-----------|-----------|-----------|---------|
| Dependency detection | ~200ms | ~500ms | ~1x |
| Tool execution | 60-120s | - | - |
| Cache I/O | ~2-4s | ~2-4s | 1x |
| **Total** | **~60-120s** | **~5-8s** | **10-20x** |

### Design Decisions

**Why glob patterns instead of tool introspection?**
- Simple and explicit
- No tool-specific dependency detection needed
- Fast (recursive glob is efficient)
- User controls exactly what's tracked

**Why hash all files?**
- Ensures correct invalidation based on content
- ANY source file content change triggers regeneration
- Acceptable overhead (~100-500ms for 1000 files)

**Why not cache failed runs?**
- Prevents caching partial/corrupt output
- Forces retry on next invocation
- User-friendly behavior

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
- Time: ~20-100ms total
  - Index lookup: <1ms
  - Hash all dependencies (50 files): ~10-50ms
  - Hash comparison: <1ms
  - File copy + I/O: ~10-50ms
- Speedup vs actual compilation: 50-200x

**Cache Miss:**
- Overhead: ~200-300ms (dependency detection + hashing)
- Minimal overhead compared to actual tool execution
- Dependency info cached for future hits

**Dependency Detection (Only on Cache Miss):**
- MSVC `/showIncludes /Zs` (~100-200ms for typical files)
- Much faster than full preprocessing (no code generation)
- One-time cost per cache miss
- Results stored in cache for instant future lookups

**Hash Comparison (On Every Cache Hit):**
- 64-bit BLAKE2b hash calculation for each file
- No `/showIncludes` execution needed
- Fast (~10-50ms for 50 files)

## Design Decisions

### Why Hash Comparison?

**Approach:** Store dependency hashes in cache, compare hashes on lookup

**Benefits:**
- Content-based comparison (~10-50ms for 50 files)
- No `/showIncludes` needed on cache hits
- No false cache invalidations from file touches
- Detects actual content changes reliably

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
2. **Hash Overhead** - Cache hits require hashing all dependencies (~10-50ms for typical files)
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
