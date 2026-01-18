# Quicken User Guide

Quicken is a caches C++ artifacts like object files. 
* If there is a cache miss and the tools is successful, Quicken, stores the artifacts in a cache.
* When there is a cache hit, 
  * Quicken restores the artifacts from the cache instead of running the tool again. 
  * Artifacts are created and modified files, stdout and stderr. 
  * The tool return code is preserved (but currently only tool runs with return code 0 is cached).
  * stdout and stderr are uppdated with current file paths.
  * Restored files get a new time stamp
  * Restored files may overwrte existing files.
* There will be a file-level cache hit when the cpp/dependent files
    * Are the same size and mtime as the cached version
    * Have only single-line whitespace or comment edits
* There will be a cache it when the file and dependencies all have file-level cache hits.  

## Import and Initialize

```python
from pathlib import Path
from quicken import Quicken

# Initialize with repository directory
quicken = Quicken(repo_dir=Path.cwd())
```

## Compile Files

```python
# Create a reusable compiler command
cl = quicken.cl(tool_args=["/c", "/W4"], output_args=[], input_args=[])

# Compile a single file
stdout, stderr, returncode = quicken.run(Path("myfile.cpp"), cl)

if returncode != 0:
    print("Compilation failed!")
```

## API Reference

 `Quicken.__init__(repo_dir, cache_dir=None)`

Initialize Quicken instance.

**Parameters:**
- `repo_dir` (Path): Repository root directory (required)
- `cache_dir` (Path, optional): Cache directory path (defaults to `~/.quicken/cache`)

**Example:**
```python
quicken = Quicken(repo_dir=Path.cwd())
```

`Quicken.cl(tool_args, output_args, input_args, optimization=None) -> CmdTool`

Create a reusable MSVC cl compiler command.

**Parameters:**
- `tool_args` (List[str]): Arguments to pass to the tool
- `output_args` (List[str]): Output-specific arguments not part of cache key
- `input_args` (List[str]): Input-specific arguments part of cache key
- `optimization` (int, optional): Optimization level (0-3), or None to accept any cached level

**Returns:**
- `CmdTool`: Reusable tool command object

---

`Quicken.clang(tool_args, output_args, input_args, optimization=None) -> CmdTool`

Create a reusable clang++ compiler command.

**Parameters:** Same as `cl()`

---

`Quicken.clang_tidy(tool_args, output_args, input_args) -> CmdTool`

Create a reusable clang-tidy command.

**Parameters:**
- `tool_args` (List[str]): Arguments to pass to the tool
- `output_args` (List[str]): Output-specific arguments not part of cache key
- `input_args` (List[str]): Input-specific arguments part of cache key

---

`Quicken.doxygen(tool_args, output_args, input_args) -> CmdTool`

Create a reusable doxygen command.

**Parameters:** Same as `clang_tidy()`

---

`Quicken.run(file, tool_cmd) -> Tuple[str, str, int]`

Execute a tool on a file (with caching).

**Parameters:**
- `file` (Path): File to process (C++ file for compilers, Doxyfile for Doxygen)
- `tool_cmd` (CmdTool): Tool command created by `cl()`, `clang()`, `clang_tidy()`, or `doxygen()`

**Returns:**
- `Tuple[str, str, int]`: (stdout, stderr, returncode)

**Example:**
```python
cl = quicken.cl(tool_args=["/c", "/W4"], output_args=[], input_args=[])
stdout, stderr, returncode = quicken.run(Path("myfile.cpp"), cl)
```

---

`Quicken.clear_cache()`

Clear the entire cache.

## Configuration

Tools are configured in `~/.quicken/tools.json` (created during installation).

## See Also
- `test/example_library_usage.py` - Comprehensive examples with benchmarks
- `CLAUDE.md` - Architecture documentation
