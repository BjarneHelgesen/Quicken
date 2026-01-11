# Quicken - Implementation Documentation

## Overview

Quicken is an **independent, standalone** Python library that provides caching for C++ build tools. It dramatically speeds up repeated compilation and analysis by caching tool outputs based on local file dependencies and file hashes.

**IMPORTANT: Independence**
- Can be used as a Python library: `from quicken import Quicken`
- Can be integrated into any build system or project
- Maintains its own configuration (`tools.json`)
- Requires a file system with high accuracy. E.g. local NTFS, not containeraized or network drives.
- Dont make any changes backward compatible. Users will have to clear their cache and update their api calls 
 
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
1. Look up the cached entry based on repo-relative path, tool and arguments. 
   - If it does not exist, we have a Cache MISS
   - If it exists, but the file size is different, we have a Cache MISS.
   - If the mtime matches → File HIT (maybe a Cache hit)!
   - If the mtime does not match, but the hashes matche → File HIT (maybe a Cache hit)!
   - If we have a File hit, test all dependencies. If we get file hit for all dependencies, we have a Cache HIT!

**Cache Miss (Slow Path - Runs Tool):**
1. Run MSVC `/showIncludes` to detect dependencies for Cpp files 
2. Execute the actual tool
3. Store output files and dependency hashes for future hits

**Performance Characteristics:**
- Cache hits require hashing all dependencies 
- `/showIncludes` only runs on cache misses when tool execution dominates anyway
- Optimized for scenarios with many cache hits per miss

This ensures that:
- Same local files with same content → instant cache hit
- Different flags → different cache entries (separate lookup)
- Header changes → cache invalidation (hash comparison fails)
- External library changes → ignored (not tracked for speed)
- **Cache portability → works across different checkout locations** (uses repo.relative path - not absolute path)


### Request Logging

Quicken automatically logs every request to `~/.quicken/quicken.log` with information about cache hits and misses.


## Implementation Details


## Repo-Level Tool Caching

Quicken supports caching for **repo-level tools** (e.g., Doxygen, cppcheck) that operate on entire repositories rather than individual source files.

 
### Multiple Runs with Different Configurations

Each run with a different main file creates a separate cache entry:


Both entries share the same dependencies (all C++ files), so both invalidate together when source changes.


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

**Note:** Optimization flags are defined in code (in ToolCmd subclasses), not in the config file. This ensures consistent behavior across installations.

## Design Decisions

### Why Hash Comparison?

**Approach:** Store dependency mtime, sizes and hashes in cache, compare on lookup

**Benefits:**
- mtime and size comparison is very fast
- Content-based comparison (hasing ~10-50ms for 50 files, is still fast if mtime has changed for the same content)
- No `/showIncludes` needed on cache hits
- No false cache invalidations from file touches
- Detects actual content changes reliably

### Why Store Dependencies in Cache?

**Approach:** Detect dependencies once on cache miss, store for future lookups

**Benefits:**
- `/showIncludes` only runs on cache misses
- Cache hits avoid expensive dependency detection
- Optimal for scenarios with many cache hits per miss



## Testing
Test are categories as follows: 
* pedantic: Low leve tests that are covered by other tests. Useful when there is a regression
* regression_test: Tests that verify previously fixed bugs remain fixed
* The rest are unit tests


### Unit Tests

See `unit_tests/` directory 

### Regression Tests

Regression tests verify that previously fixed bugs remain fixed. They are stored in `regression_test/` directory and marked with `@pytest.mark.regression_test`.

**When to Create a Regression Test:**

When a bug is found, the user should create a failing regression test BEFORE the fix is implemented:

1. **User finds a bug** - Create a regression test that demonstrates the bug (test fails)
2. **Fix the bug** - Update the code to fix the issue
3. **Verify the fix** - The regression test should now pass
4. **Commit together** - Commit both the fix and the regression test

**If API is Used Incorrectly:**
- Document legal and illegal usage patterns
- Add error handling for incorrect usage
- Either update the regression test to verify error handling, or delete it

**If API is Used Correctly:**
- Fix the bug in the implementation
- Ensure the regression test passes
- Commit both the fix and the test together



**Example Regression Test:**

See `regression_test/test_cache_entry_reuse_regression.py` for a complete example. .

Key elements of a good regression test:
- Clear documentation of the bug
- Uses the Quicken API correctly (as users would use it)
- Marked with `@pytest.mark.regression_test`

### Expected Behavior

- First run with a given file and tool configuration: Cache miss, tool executes normally
- Subsequent runs with same file and unchanged dependencies: Cache hit, instant results
- Modified source or header files: Cache miss, tool re-executes
- Different tool arguments: Cache miss (separate cache entries)
- Output files restored with same content as original tool execution
- stdout/stderr identical to direct tool execution

## Conclusion

Quicken provides transparent caching for C++ build tools with:
- Artifacts are retrieved in about 1ms when there is a cache hit
- Minimal overhead on cache miss (~100-200ms)
- Massive speedup on cache hit
- Easy integration with existing workflows
- Simple, maintainable codebase

The metadata-based approach prioritizes speed while the local-dependency tracking ensures practical correctness for iterative development.


## Important: Additional Instructions for Claude
- Don't commit changes unless asked to commit (using the word commit)
- Don't run unit tests or performance tests unless specifically asked to do so
- Don't create **documents** explaining issues unless specifically asked to create documents
- Do only what is requested. If more tasks are necessary, ask to clarify
- Comment code minimally, only where necessary for clarity
- When updating code, update unit tests also
- When making unit tests, don't make tests that prints results. Make proper pytest unit tests with asserts. 
- When a unit tests passes where the operation did not succeed, make the unit tests stricter or make a new unit test to cover the issue
- Put functionality *in* classes, rather than in code using the classes, when possible. I.e. extend the classes rather than put related logic outside