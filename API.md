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
# Compile a single file
stdout, stderr, returncode = quicken.run(
    source_file=Path("myfile.cpp"),
    tool_name="cl",
    tool_args=["/c", "/W4"]
)

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

`Quicken.run(source_file, tool_name, tool_args, optimization=None, output_args=None, input_args=[]) -> Tuple[str, str, int]`

Execute a tool on a C++ file (with caching).

**Parameters:**
- `source_file` (Path): Source file to process
- `tool_name` (str): Tool name from `~/.quicken/tools.json` (e.g., "cl", "clang-tidy")
- `tool_args` (List[str]): Arguments to pass to the tool
- `optimization` (int, optional): Optimization level (0-3), or None to accept any cached level
- `output_args` (List[str], optional): Output-specific arguments not part of cache key
- `input_args` (List[str], optional): Input-specific arguments part of cache key

**Returns:**
- `Tuple[str, str, int]`: (stdout, stderr, returncode)

**Example:**
```python
stdout, stderr, returncode = quicken.run(
    source_file=Path("myfile.cpp"),
    tool_name="cl",
    tool_args=["/c", "/W4"]
)
```

## Configuration

Tools are configured in `~/.quicken/tools.json` (created during installation).

## See Also
- `test/example_library_usage.py` - Comprehensive examples with benchmarks
- `CLAUDE.md` - Architecture documentation
