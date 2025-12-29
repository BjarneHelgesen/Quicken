# Persistent Test Fixture Cache

## Overview

The persistent fixture cache dramatically speeds up test iterations by caching tool results between test runs.

**Performance:**
- **First run (cold cache):** ~107 seconds (tests populate cache naturally)
- **Subsequent runs (warm cache):** ~3-5 seconds ⚡ **30x faster!**

## How It Works

1. **Session startup:**
   - Checks if persistent cache exists in `~/.quicken/test_fixture_cache/<hash>/`
   - Checks if persistent temp dir exists in `~/.quicken/test_temp/<hash>/`
   - Both use the same hash based on tools.json + test code

2. **First run (cold):**
   - Tests create files in persistent temp dir
   - Tools run normally
   - Results cached in persistent cache dir

3. **Subsequent runs (warm):**
   - Tests use same files in persistent temp dir
   - Cache lookups succeed (same file paths + content)
   - No tools run - instant cache hits!

## Cache Invalidation

The cache is automatically invalidated (new hash generated) when:
- ✅ `tools.json` changes (different tool paths)
- ✅ Test source code changes (`SIMPLE_CPP_CODE`, `CPP_CODE_WITH_WARNING`)

## Manual Cache Management

```bash
# Clear cache and temp files (forces complete rebuild on next run)
rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/

# Skip pedantic tests (faster)
pytest -m "not pedantic"
```

**Note:** Both cache AND temp directories must be cleared together to force rebuild.

## Test Markers

### `@pytest.mark.pedantic`
Tests that verify edge cases or redundant scenarios.

**Run during:**
- ❌ Development iterations (skip with `-m "not pedantic"`)
- ✅ CI/CD (run all tests)
- ✅ Before commits (verification)

## Fixture Usage

All tests automatically use the persistent cache via the `quicken_instance` fixture:

```python
def test_msvc_cache_miss_and_hit(quicken_instance, test_cpp_file):
    # Automatically uses persistent cache
    returncode = quicken_instance.run(test_cpp_file, "cl", ["/c", "/nologo", "/EHsc"], ...)
    # Tool result comes from cache (~0.05s instead of ~1.4s)
```

Tests that modify files or expect cache misses will still work - they just start with a pre-populated cache and add their own entries.

## What Gets Cached

The persistent cache stores all tool invocations from the test suite:

**Populated on first run:**
- All MSVC (cl) compilations with various flags
- All Clang++ compilations with different optimization levels
- All clang-tidy analysis runs with different check sets
- Output files (.obj, .o) from compilations
- stdout/stderr from all tool executions

**Reused on subsequent runs:**
- Tests get cache hits for matching tool invocations
- No actual tools run (except for cache invalidation tests)
- Dramatic speedup from avoiding subprocess overhead

## Workflow Examples

### Development Iteration
```bash
# First run: populates cache (~107s)
pytest -m "not pedantic"

# Edit code, run again: uses cache (~3-5s) ⚡
pytest -m "not pedantic"

# Edit code, run again: uses cache (~3-5s) ⚡
pytest -m "not pedantic"
```

### After Changing Tools or Test Code
```bash
# Clear cache and temp (triggers automatic rebuild on next run)
rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/

# Run tests: will populate new cache (~107s)
pytest
```

### CI/CD
```bash
# Option 1: Let CI build and persist cache
pytest

# Option 2: Clear everything each time for consistency
rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/
pytest
```

## Architecture

```
conftest.py
├── persistent_tool_cache (session scope)
│   └── Loads/creates cache in ~/.quicken/test_fixture_cache/<hash>/
│
├── persistent_temp_dir (session scope)
│   └── Loads/creates temp dir in ~/.quicken/test_temp/<hash>/
│
├── temp_dir (function scope)
│   └── Test-specific subdirectory: ~/.quicken/test_temp/<hash>/<test_name>/
│
├── quicken_with_persistent_cache (function scope)
│   └── Quicken instance using persistent cache (shared across tests)
│
└── quicken_instance (function scope)
    └── Alias for quicken_with_persistent_cache
```

**Key insight:** Tests use the SAME temp directory and SAME cache across runs,
ensuring file paths and cache keys remain consistent for cache hits.

## Troubleshooting

**Cache not being reused:**
- Check that tools.json hasn't changed
- Check that SIMPLE_CPP_CODE/CPP_CODE_WITH_WARNING haven't changed
- Verify directories exist: `ls ~/.quicken/test_fixture_cache/` and `ls ~/.quicken/test_temp/`
- Clear and rebuild: `rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/`

**Tests failing:**
- Some tests (e.g., cache invalidation tests) modify files and expect cache misses
- They'll still work correctly with persistent cache
- Clear everything if needed: `rm -rf ~/.quicken/test_fixture_cache/ ~/.quicken/test_temp/`

**Slow first run:**
- Expected! First run takes ~107s to populate cache
- Subsequent runs will be ~3-5s
- Cache persists across test sessions

## Benefits

✅ **Fast development iterations:** 3s instead of 107s
✅ **Real tool testing:** Cache contains actual tool outputs
✅ **Test isolation:** Each test gets its own cache copy
✅ **Automatic invalidation:** Cache rebuilds when tools/code changes
✅ **Simple design:** No special markers needed, just clear cache when needed
