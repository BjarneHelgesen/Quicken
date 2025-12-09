# Quicken

A Python command-line caching wrapper for C++ build tools (compilers, analyzers like clang-tidy).

## Overview

Quicken speeds up repeated builds and analysis runs by caching tool outputs based on the preprocessed translation unit (TU) and the exact tool command. When you run a tool on a C++ file, Quicken:

1. **Hashes**: Preprocesses the C++ file using MSVC to get the translation unit, then hashes it using BLAKE2b
2. **Looks up**: Checks if this exact TU hash + tool command combination exists in the cache
3. **Returns**: If found in cache, instantly returns the cached output files
4. **Caches**: If not found, runs the tool, stores the output in cache for next time

## Installation

1. Clone this repository
2. Ensure Python 3.7+ is installed
3. Configure `tools.json` with paths to your build tools

## Configuration

Edit `tools.json` to configure tool paths:

```json
{
  "cl": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\MSVC\\14.44.35207\\bin\\Hostx64\\x64\\cl.exe",
  "vcvarsall": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvarsall.bat",
  "msvc_arch": "x64",
  "clang-tidy": "clang-tidy",
  "clang": "clang++"
}
```

## Usage

```bash
python quicken.py <cpp_file> <tool> [tool_args...]
```

### Examples

Compile with MSVC:
```bash
python quicken.py myfile.cpp cl /c /W4
```

Run clang-tidy:
```bash
python quicken.py myfile.cpp clang-tidy --checks=*
```

Compile with Clang:
```bash
python quicken.py myfile.cpp clang++ -c -Wall
```

### Custom Config Location

```bash
python quicken.py myfile.cpp cl /c --config /path/to/tools.json
```

## Cache Location

Cache is stored in `~/.quicken/cache/` by default.

## How It Works

1. **Translation Unit Preprocessing**: Uses MSVC's preprocessor (`cl /E`) to expand all includes and macros, creating a complete translation unit
2. **Fast Hashing**: Hashes the TU using BLAKE2b (fast cryptographic hash)
3. **Command-Aware Caching**: Cache key combines TU hash + tool command, so different compiler flags create different cache entries
4. **File Restoration**: Cached entries include all output files (`.obj`, `.exe`, `.pdb`, etc.) and are restored with original timestamps

## Benefits

- Instant results for unchanged files (even with different timestamps)
- Works across different build systems
- Transparent - same output as running the tool directly
- Tool-agnostic - works with any command-line tool

## Requirements

- Python 3.7+
- MSVC (for preprocessing)
- Target tools (cl, clang-tidy, etc.) configured in tools.json
