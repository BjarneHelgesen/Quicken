# Quicken - Implementation Documentation

## Overview

Quicken is an **independent, standalone** Python library that provides caching for C++ build tools. It dramatically speeds up repeated compilation and analysis by caching tool outputs based on local file dependencies and file hashes.

Quicken works best on a file system with high resolution mtime (e.g. local NTFS, not traditional FAT or containerized/network drives)

## Package Structure

```
quicken/
  __init__.py          # Public API - exports Quicken class
  _quicken.py          # Main Quicken class implementation
  _cache.py            # Cache storage and retrieval
  _tool_cmd.py         # Tool command execution
  _cpp_normalizer.py   # C++ output normalization
  _repo_file.py        # Repository path handling
cleanup.py             # Cache management CLI
test/
  unit/                # Unit tests
  regression/          # Regression tests (marked with @pytest.mark.regression_test)
```

**Usage:**
```python
from quicken import Quicken

quicken = Quicken(repo_dir=Path.cwd())
cl = quicken.cl(tool_args=["/c", "/W4"], output_args=[], input_args=[])
stdout, stderr, returncode = quicken.run(Path("main.cpp"), cl)
```

## Caching Strategy

**OPTIMIZED FOR CACHE HITS** - Assumes 10-100 cache hits per cache miss.

**Cache Lookup (Fast Path):**
1. Lookup by repo-relative path, tool, and arguments
2. Compare file size, mtime, then hash if needed
3. Validate all dependencies similarly
4. Cache HIT → restore artifacts (~1ms)

**Cache Miss (Slow Path):**
1. Run MSVC `/showIncludes` to detect dependencies
2. Execute tool 
3. Store output files and dependency hashes

**Key Properties:**
- Same content → instant cache hit
- Different flags → separate cache entries
- Header changes → invalidation via hash comparison
- External libraries → not tracked (for speed)
- Cache portable → uses repo-relative paths

## Configuration

`tools.json` at repository root:
```json
{
  "tool_name": "/path/to/tool",
  "vcvarsall": "path/to/vcvarsall.bat",
  "msvc_arch": "x64"
}
```

## Cache Cleanup

`cleanup.py` - standalone CLI, Nuitka-compilable

```
Main arguments: 
--stats                  
--clear                  
```

--clear accepts filters for what to clear. Run --help for full syntax

## Testing

Run tests with: `python test.py`

**Test Categories:**
- Unit tests: `test/unit/`
- Regression tests: `test/regression/` (marked `@pytest.mark.regression_test`)
- Pedantic tests: Low-level tests covered by other tests (useful for debugging)

**Regression Test Workflow:**
1. Bug found → Create failing regression test
2. Fix bug → Update implementation
3. Verify test passes → Commit fix and test together


## Code Style

**Minimal Documentation:**
- Prioritize clarity through simple, readable code
- Comments only where necessary for clarity
- Docstrings only for non-obvious information

**Design Principles:**
- Put functionality inside classes (extend classes vs external logic)
- No backward compatibility needed (users clear cache and update API calls)
- Optimize for cache hit performance
- Regular classes with explicit `__init__` (no `@dataclass`)

## Workflow

**Testing:** Run `python test.py` after code changes (don't run pytest directly)

**Commits:** Only commit when specifically instructed to do so. Multiple commits for clarity is fine.

**Unit Tests:**
- Update tests when modifying code
- Tests should assert only, not print results
- Make tests stricter if they pass when operation fails 

- After modifying code, run test.py to verify that unit tests pass. Don't run unit tests directly or any other tests
- Don't create any documents explaining issues unless specifically asked to create documents
- Do only what is requested. If more tasks are necessary, ask to clarify

