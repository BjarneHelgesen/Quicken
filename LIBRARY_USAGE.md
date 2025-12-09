# Quicken Library Usage Guide

## Why Use Quicken as a Library?

When calling Quicken hundreds or thousands of times from a Python program, using it as a library instead of a subprocess provides **40-100x speedup** by eliminating Python interpreter startup overhead.

## Performance Comparison

| Method | Per-call Overhead | 1000 Calls |
|--------|------------------|------------|
| Subprocess (`python quicken.py ...`) | ~40-100ms | ~40-100 seconds |
| Library (`quicken.run(...)`) | <1ms | <1 second |

## Basic Usage

### Import and Initialize

```python
from pathlib import Path
from quicken import Quicken

# Initialize once (reuse for all compilations)
quicken = Quicken(Path("tools.json"))
```

### Compile Files

```python
# Compile a single file
returncode = quicken.run(
    cpp_file=Path("myfile.cpp"),
    tool_name="cl",
    tool_args=["/c", "/W4"]
)

if returncode != 0:
    print("Compilation failed!")
```

### Process Multiple Files

```python
files = ["file1.cpp", "file2.cpp", "file3.cpp"]
failed = []

for cpp_file in files:
    returncode = quicken.run(
        cpp_file=Path(cpp_file),
        tool_name="cl",
        tool_args=["/c"]
    )
    if returncode != 0:
        failed.append(cpp_file)

print(f"Compiled {len(files) - len(failed)}/{len(files)} files")
```

## Quiet Mode (Recommended for Library Usage)

By default, Quicken prints debug messages to stderr. For production use, disable verbose output:

```python
# Initialize with verbose=False
quicken = Quicken(Path("tools.json"), verbose=False)

# Now only tool output (stdout/stderr) is shown
# No "[Quicken] Processing..." messages
for cpp_file in files:
    quicken.run(Path(cpp_file), "cl", ["/c"])
```

**When to use verbose mode:**
- `verbose=True` (default): CLI usage, debugging, development
- `verbose=False`: Library usage, production builds, CI/CD pipelines

## Complete Example

```python
#!/usr/bin/env python3
from pathlib import Path
from quicken import Quicken

def build_project(source_files: list[str]) -> bool:
    """Build all source files using Quicken."""

    # Initialize once with quiet mode
    quicken = Quicken(Path("tools.json"), verbose=False)

    # Compile all files
    failed = []
    for cpp_file in source_files:
        returncode = quicken.run(
            cpp_file=Path(cpp_file),
            tool_name="cl",
            tool_args=["/c", "/W4", "/O2"]
        )

        if returncode != 0:
            failed.append(cpp_file)

    # Report results
    success = len(source_files) - len(failed)
    print(f"Build complete: {success}/{len(source_files)} files")

    if failed:
        print(f"Failed files:")
        for f in failed:
            print(f"  - {f}")
        return False

    return True

if __name__ == "__main__":
    files = ["main.cpp", "utils.cpp", "parser.cpp"]
    success = build_project(files)
    exit(0 if success else 1)
```

## Advanced: Parallel Compilation

For large projects, use parallel execution:

```python
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from quicken import Quicken

def compile_file(quicken: Quicken, cpp_file: str):
    """Compile a single file."""
    return quicken.run(Path(cpp_file), "cl", ["/c"])

def parallel_build(files: list[str], workers: int = 4):
    """Build files in parallel."""
    quicken = Quicken(Path("tools.json"), verbose=False)

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {executor.submit(compile_file, quicken, f): f
                   for f in files}

        failed = []
        for future in futures:
            if future.result() != 0:
                failed.append(futures[future])

        return failed

# Build 100 files using 4 parallel workers
files = [f"file{i}.cpp" for i in range(100)]
failed = parallel_build(files)
print(f"Failed: {len(failed)}")
```

## API Reference

### `Quicken.__init__(config_path, verbose=True)`

Initialize Quicken instance.

**Parameters:**
- `config_path` (Path): Path to `tools.json` configuration file
- `verbose` (bool, optional): Enable debug output. Default: `True`

**Example:**
```python
# Verbose mode (CLI-style)
quicken = Quicken(Path("tools.json"), verbose=True)

# Quiet mode (library-style)
quicken = Quicken(Path("tools.json"), verbose=False)
```

### `Quicken.run(cpp_file, tool_name, tool_args) -> int`

Execute a tool on a C++ file (with caching).

**Parameters:**
- `cpp_file` (Path): C++ source file to process
- `tool_name` (str): Tool name from `tools.json` (e.g., "cl", "clang-tidy")
- `tool_args` (List[str]): Arguments to pass to the tool

**Returns:**
- `int`: Tool exit code (0 = success)

**Example:**
```python
returncode = quicken.run(
    cpp_file=Path("myfile.cpp"),
    tool_name="cl",
    tool_args=["/c", "/W4", "/O2"]
)
```

## Migration from Subprocess

### Before (Subprocess)
```python
import subprocess

for cpp_file in files:
    subprocess.run([
        "python", "quicken.py",
        cpp_file, "cl", "/c"
    ])
```

### After (Library)
```python
from pathlib import Path
from quicken import Quicken

quicken = Quicken(Path("tools.json"), verbose=False)

for cpp_file in files:
    quicken.run(Path(cpp_file), "cl", ["/c"])
```

## See Also

- `example_library_usage.py` - Comprehensive examples with benchmarks
- `quicken.py` - Full implementation
- `CLAUDE.md` - Architecture documentation
