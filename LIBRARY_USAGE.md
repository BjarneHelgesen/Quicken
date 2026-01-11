# Quicken Library Usage Guide

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
returncode = quicken.run(
    source_file=Path("myfile.cpp"),
    tool_name="cl",
    tool_args=["/c", "/W4"]
)

if returncode != 0:
    print("Compilation failed!")
```

## API Reference

### `Quicken.__init__(repo_dir, cache_dir=None)`

Initialize Quicken instance.

**Parameters:**
- `repo_dir` (Path): Repository root directory (required)
- `cache_dir` (Path, optional): Cache directory path (defaults to `~/.quicken/cache`)

**Example:**
```python
quicken = Quicken(repo_dir=Path.cwd())
```

### `Quicken.run(source_file, tool_name, tool_args, optimization=None, output_args=None, input_args=[]) -> int`

Execute a tool on a C++ file (with caching).

**Parameters:**
- `source_file` (Path): Source file to process
- `tool_name` (str): Tool name from `~/.quicken/tools.json` (e.g., "cl", "clang-tidy")
- `tool_args` (List[str]): Arguments to pass to the tool
- `optimization` (int, optional): Optimization level (0-3), or None to accept any cached level
- `output_args` (List[str], optional): Output-specific arguments not part of cache key
- `input_args` (List[str], optional): Input-specific arguments part of cache key

**Returns:**
- `int`: Tool exit code (0 = success)

**Example:**
```python
returncode = quicken.run(
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
