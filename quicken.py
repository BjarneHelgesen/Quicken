#!/usr/bin/env python3
"""
Quicken - A caching wrapper for C++ build tools

Quicken caches the output of C++ tools (compilers, analyzers like clang-tidy)
based on local file dependencies (using MSVC /showIncludes) and file metadata
(size + mtime). External libraries are ignored for caching to maximize speed.
"""

import argparse
import hashlib
import json
import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple


class QuickenCache:
    """Manages caching of tool outputs based on source file and dependency metadata."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = cache_dir / "index.json"
        self.index = self._load_index()
        self._next_id = self._get_next_id()

    def _load_index(self) -> Dict:
        """Load the cache index.

        Index structure:
        {
            "source_file_path": [
                {
                    "cache_key": "entry_001",
                    "tool_cmd": "cl /c /W4",
                    "dependencies": [
                        {"path": "C:\\path\\file.cpp", "size": 1234, "mtime_ns": 132456789},
                        ...
                    ]
                },
                ...
            ],
            ...
        }
        """
        if self.index_file.exists():
            with open(self.index_file, 'r') as f:
                return json.load(f)
        return {}

    def _save_index(self):
        """Save the cache index."""
        with open(self.index_file, 'w') as f:
            json.dump(self.index, f, indent=2)

    def _get_next_id(self) -> int:
        """Get next available cache entry ID."""
        max_id = 0
        for entries in self.index.values():
            for entry in entries:
                cache_key = entry.get("cache_key", "")
                if cache_key.startswith("entry_"):
                    try:
                        entry_id = int(cache_key.split("_")[1])
                        max_id = max(max_id, entry_id)
                    except (ValueError, IndexError):
                        pass
        return max_id + 1

    def _get_file_metadata(self, file_path: Path) -> Dict:
        """Get metadata for a single file."""
        stat = file_path.stat()
        return {
            "path": str(file_path.resolve()),
            "size": stat.st_size,
            "mtime_ns": stat.st_mtime_ns
        }

    def _dependencies_match(self, cached_deps: List[Dict]) -> bool:
        """Check if all cached dependencies still match their metadata."""
        for dep in cached_deps:
            dep_path = Path(dep["path"])
            if not dep_path.exists():
                return False
            stat = dep_path.stat()
            if stat.st_size != dep["size"] or stat.st_mtime_ns != dep["mtime_ns"]:
                return False
        return True

    def lookup(self, source_file: Path, tool_cmd: str) -> Optional[Path]:
        """Look up cached output for given source file and tool command.

        This is the optimized fast path that doesn't run /showIncludes.
        It only checks file metadata (size + mtime) against cached values.
        """
        source_key = str(source_file.resolve())

        if source_key not in self.index:
            return None

        # Check each cached entry for this source file
        for entry in self.index[source_key]:
            # Check if tool command matches
            if entry["tool_cmd"] != tool_cmd:
                continue

            # Check if all dependencies still match their cached metadata
            if not self._dependencies_match(entry["dependencies"]):
                continue

            # Cache hit! Return the cache entry directory
            cache_entry_dir = self.cache_dir / entry["cache_key"]
            if cache_entry_dir.exists():
                return cache_entry_dir

        return None

    def store(self, source_file: Path, tool_cmd: str, dependencies: List[Path],
              output_files: List[Path], stdout: str, stderr: str, returncode: int,
              repo_mode: bool = False, dependency_patterns: List[str] = None,
              output_base_dir: Path = None) -> Path:
        """Store tool output in cache with dependency metadata.

        Args:
            source_file: Source file path (or main file for repo mode)
            tool_cmd: Tool command string
            dependencies: List of dependency file paths
            output_files: List of output file paths
            stdout: Tool stdout
            stderr: Tool stderr
            returncode: Tool exit code
            repo_mode: If True, this is a repo-level cache entry
            dependency_patterns: Glob patterns used (for repo mode)
            output_base_dir: Base directory for preserving relative paths

        Returns:
            Path to cache entry directory
        """
        source_key = str(source_file.resolve())

        # Generate unique cache key
        cache_key = f"entry_{self._next_id:06d}"
        self._next_id += 1

        cache_entry_dir = self.cache_dir / cache_key
        cache_entry_dir.mkdir(parents=True, exist_ok=True)

        # Store output files (with directory structure for repo mode)
        stored_files = []
        for output_file in output_files:
            if output_file.exists():
                # Calculate relative path from output_base_dir if provided
                if output_base_dir:
                    try:
                        rel_path = output_file.relative_to(output_base_dir)
                        dest = cache_entry_dir / rel_path
                        file_path_str = str(rel_path)
                    except ValueError:
                        # File is outside output_base_dir, use just filename
                        dest = cache_entry_dir / output_file.name
                        file_path_str = output_file.name
                else:
                    dest = cache_entry_dir / output_file.name
                    file_path_str = output_file.name

                # Create parent directories as needed
                dest.parent.mkdir(parents=True, exist_ok=True)

                # Copy file with metadata
                shutil.copy2(output_file, dest)
                stored_files.append(file_path_str)

        # Collect dependency metadata
        dep_metadata = [self._get_file_metadata(dep) for dep in dependencies]

        # Store metadata
        metadata = {
            "cache_key": cache_key,
            "source_file": source_key,
            "tool_cmd": tool_cmd,
            "dependencies": dep_metadata,
            "files": stored_files,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode,
            "repo_mode": repo_mode
        }

        if repo_mode and dependency_patterns:
            metadata["dependency_patterns"] = dependency_patterns

        with open(cache_entry_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        # Update index
        if source_key not in self.index:
            self.index[source_key] = []

        index_entry = {
            "cache_key": cache_key,
            "tool_cmd": tool_cmd,
            "dependencies": dep_metadata
        }

        if repo_mode:
            index_entry["repo_mode"] = True
            if dependency_patterns:
                index_entry["dependency_patterns"] = dependency_patterns

        self.index[source_key].append(index_entry)
        self._save_index()

        return cache_entry_dir

    def restore(self, cache_entry_dir: Path, output_dir: Path) -> Tuple[str, str, int]:
        """Restore cached files to output directory.

        Handles both flat files and directory trees using relative paths.
        """
        metadata_file = cache_entry_dir / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        # Restore output files (preserving directory structure)
        for file_path_str in metadata["files"]:
            # file_path_str may be relative path (e.g., "xml/index.xml")
            src = cache_entry_dir / file_path_str
            dest = output_dir / file_path_str

            if src.exists():
                # Create parent directories if needed
                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dest)

        return metadata["stdout"], metadata["stderr"], metadata["returncode"]

    def clear(self):
        """Clear all cached entries."""
        # Remove all cache entry directories
        if self.cache_dir.exists():
            for entry in self.cache_dir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry)
                elif entry != self.index_file:
                    entry.unlink()

        # Clear the index
        self.index = {}
        self._save_index()


class Quicken:
    """Main Quicken application."""

    def __init__(self, config_path: Path, verbose: bool = True):
        self.config = self._load_config(config_path)
        self.cache = QuickenCache(Path.home() / ".quicken" / "cache")
        self.verbose = verbose
        self._msvc_env = None  # Cached MSVC environment

    def _load_config(self, config_path: Path) -> Dict:
        """Load tools configuration."""
        if not config_path.exists():
            raise FileNotFoundError(f"Config file not found: {config_path}")

        with open(config_path, 'r') as f:
            return json.load(f)

    def _get_tool_path(self, tool_name: str) -> str:
        """Get the full path to a tool from config."""
        if tool_name not in self.config:
            raise ValueError(f"Tool '{tool_name}' not found in config")
        return self.config[tool_name]

    def _get_msvc_environment(self) -> Dict:
        """Get MSVC environment variables (cached after first call)."""
        if self._msvc_env is not None:
            return self._msvc_env

        vcvarsall = self._get_tool_path("vcvarsall")
        msvc_arch = self.config.get("msvc_arch", "x64")

        # Run vcvarsall and capture environment
        cmd = f'"{vcvarsall}" {msvc_arch} >nul && set'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse environment variables from output
        env = os.environ.copy()
        for line in result.stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                env[key] = value

        self._msvc_env = env
        return self._msvc_env

    def _add_optimization_flag(self, tool_name: str, tool_args: List[str],
                              optimization: Optional[int]) -> List[str]:
        """Insert optimization flag into tool arguments if tool supports optimization.

        Args:
            tool_name: Name of tool (cl, clang++, etc.)
            tool_args: Original tool arguments
            optimization: Optimization level (None = use 0)

        Returns:
            Modified tool_args with optimization flag inserted at beginning,
            or original tool_args if tool doesn't support optimization
        """
        # Get optimization flags from config
        optimization_flags = self.config.get("optimization_flags", {})

        # If this tool doesn't support optimization, return args unchanged
        if tool_name not in optimization_flags:
            return tool_args

        # Default to O0 if not specified
        opt_level = optimization if optimization is not None else 0

        # Validate range
        if opt_level < 0 or opt_level > 3:
            raise ValueError(f"optimization must be 0-3 or None, got {opt_level}")

        # Get flags for this tool
        flags = optimization_flags[tool_name]

        if opt_level >= len(flags):
            raise ValueError(f"optimization level {opt_level} not configured for tool {tool_name}")

        opt_flag = flags[opt_level]

        # Insert at beginning of args
        return [opt_flag] + tool_args

    def _get_local_dependencies(self, cpp_file: Path, repo_dir: Path) -> List[Path]:
        """Get list of local (repo) file dependencies using MSVC /showIncludes."""
        cl_path = self._get_tool_path("cl")
        env = self._get_msvc_environment()

        # Pre-resolve repo_dir once for faster filtering
        repo_dir_resolved = repo_dir.resolve()

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        # This is much faster than full preprocessing
        try:
            result = subprocess.run(
                [cl_path, '/showIncludes', '/Zs', str(cpp_file)],
                env=env,
                capture_output=True,
                text=True,
                check=False
            )

            # Parse /showIncludes output
            # Format: "Note: including file:   <path>"
            dependencies = [cpp_file]  # Always include the source file itself

            for line in result.stderr.splitlines():  # /showIncludes outputs to stderr
                if line.startswith("Note: including file:"):
                    # Extract the file path (after "Note: including file:")
                    file_path_str = line.split(":", 2)[2].strip()
                    file_path = Path(file_path_str)

                    # Only include files within the repository
                    try:
                        file_path.resolve().relative_to(repo_dir_resolved)
                        dependencies.append(file_path)
                    except ValueError:
                        # File is outside repo, skip it
                        pass

            return dependencies
        except Exception as e:
            raise RuntimeError(f"Failed to get dependencies: {e}")

    def _get_repo_dependencies(self, repo_dir: Path, dependency_patterns: List[str]) -> List[Path]:
        """Get list of files matching dependency patterns in repository.

        Args:
            repo_dir: Repository root directory
            dependency_patterns: Glob patterns (e.g., ["*.cpp", "*.h"])

        Returns:
            Sorted list of absolute paths matching any pattern
        """
        dependencies = []
        for pattern in dependency_patterns:
            # Use recursive glob to find all matching files
            matches = repo_dir.rglob(pattern)
            dependencies.extend(matches)

        # Remove duplicates and sort for deterministic ordering
        unique_deps = sorted(set(dependencies))

        # Return absolute paths
        return [d.resolve() for d in unique_deps]

    def _run_repo_tool_impl(self, tool_name: str, tool_args: List[str],
                            work_dir: Path, output_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run repo-level tool and detect all output files.

        Args:
            tool_name: Name of tool to run
            tool_args: Arguments to pass to tool
            work_dir: Working directory for tool execution
            output_dir: Directory where tool creates output files

        Returns:
            Tuple of (output_files, stdout, stderr, returncode)
        """
        tool_path = self._get_tool_path(tool_name)

        # Build command
        cmd = [tool_path] + tool_args

        # Snapshot output directory before execution (recursively)
        files_before = set()
        if output_dir.exists():
            files_before = set(output_dir.rglob("*"))

        # Determine if we need vcvarsall environment
        needs_vcvars = tool_name in ["cl", "link"]

        if needs_vcvars:
            env = self._get_msvc_environment()
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=work_dir
            )
        else:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=work_dir
            )

        # Detect new files (recursively)
        files_after = set(output_dir.rglob("*")) if output_dir.exists() else set()
        new_files = files_after - files_before

        # Filter to actual files (not directories)
        output_files = [f for f in new_files if f.is_file()]

        return output_files, result.stdout, result.stderr, result.returncode

    def _run_tool(self, tool_name: str, tool_args: List[str], cpp_file: Path,
                  work_dir: Path = None, output_dir: Path = None) -> Tuple[List[Path], str, str, int]:
        """Run the specified tool with arguments.

        Args:
            tool_name: Name of tool to run
            tool_args: Arguments to pass to tool
            cpp_file: Path to C++ file (may be temp copy)
            work_dir: Working directory for tool execution (default: cpp_file.parent)
            output_dir: Directory to look for output files (default: work_dir)
        """
        tool_path = self._get_tool_path(tool_name)

        # Determine working directory (use original location if provided)
        cpp_file_dir = work_dir if work_dir else cpp_file.parent

        # Determine output directory (where to look for output files)
        output_directory = output_dir if output_dir else cpp_file_dir

        # If work_dir is specified and different from cpp_file location,
        # use absolute path to cpp_file so tool can find it
        if work_dir and work_dir != cpp_file.parent:
            file_arg = str(cpp_file.resolve())
        else:
            # Use just filename since we're in the same directory
            file_arg = cpp_file.name

        # Build full command
        cmd = [tool_path] + tool_args + [file_arg]

        # Snapshot files in output directory before tool execution
        files_before = set(output_directory.iterdir()) if output_directory.exists() else set()

        # Determine if we need vcvarsall environment
        needs_vcvars = tool_name in ["cl", "link"]

        if needs_vcvars:
            env = self._get_msvc_environment()
            result = subprocess.run(
                cmd,
                env=env,
                capture_output=True,
                text=True,
                check=False,
                cwd=cpp_file_dir
            )
        else:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                cwd=cpp_file_dir
            )

        # Detect output files by comparing directory contents before/after
        files_after = set(output_directory.iterdir()) if output_directory.exists() else set()
        new_files = files_after - files_before

        # Filter to only include actual output files (not directories, not source file)
        output_files = []
        for file_path in new_files:
            if file_path.is_file() and file_path != cpp_file:
                output_files.append(file_path)

        return output_files, result.stdout, result.stderr, result.returncode

    def run(self, cpp_file: Path, tool_name: str, tool_args: List[str],
            original_file: Path = None, repo_dir: Path = None,
            output_dir: Path = None, optimization: Optional[int] = None) -> int:
        """
        Main execution: optimized cache lookup, or get dependencies and run tool.

        Args:
            cpp_file: C++ file to process (may be a temp copy)
            tool_name: Tool to run
            tool_args: Arguments for the tool
            original_file: Original source file location (for dependency detection)
            repo_dir: Repository directory (for dependency filtering)
            output_dir: Directory where tool creates output files (for detection and cache restoration)
            optimization: Optimization level (0-3, or None to accept any cached level)
        """
        if not cpp_file.exists():
            print(f"Error: C++ file not found: {cpp_file}", file=sys.stderr)
            return 1

        if self.verbose:
            print(f"[Quicken] Processing {cpp_file} with {tool_name}...", file=sys.stderr)

        # Use original file for cache lookup and dependency detection, or cpp_file if not provided
        source_file = original_file if original_file else cpp_file

        # Determine repository directory
        if not repo_dir:
            repo_dir = Path.cwd()

        # Step 1: Fast cache lookup (no /showIncludes, just metadata comparison)
        if self.verbose:
            print(f"[Quicken] Looking up in cache...", file=sys.stderr)

        cache_entry = None
        modified_args = None
        tool_cmd = None

        # Check if this tool supports optimization
        optimization_flags = self.config.get("optimization_flags", {})
        tool_supports_optimization = tool_name in optimization_flags

        if tool_supports_optimization:
            # If optimization is None, try to find cache hit with ANY optimization level
            if optimization is None:
                for opt_level in range(4):  # Try 0, 1, 2, 3
                    # Add optimization flag for this level
                    test_args = self._add_optimization_flag(tool_name, tool_args, opt_level)
                    # Build tool command string for cache key
                    test_cmd = f"{tool_name} {' '.join(test_args)}"
                    # Try lookup
                    cache_entry = self.cache.lookup(source_file, test_cmd)
                    if cache_entry:
                        if self.verbose:
                            print(f"[Quicken] Found cache hit with optimization level {opt_level}", file=sys.stderr)
                        modified_args = test_args
                        tool_cmd = test_cmd
                        break

                # If no cache hit found, default to O0 for execution
                if not cache_entry:
                    modified_args = self._add_optimization_flag(tool_name, tool_args, 0)
                    tool_cmd = f"{tool_name} {' '.join(modified_args)}"
            else:
                # Specific optimization level requested
                modified_args = self._add_optimization_flag(tool_name, tool_args, optimization)
                tool_cmd = f"{tool_name} {' '.join(modified_args)}"
                cache_entry = self.cache.lookup(source_file, tool_cmd)
        else:
            # Tool doesn't support optimization - use args as-is
            modified_args = tool_args
            tool_cmd = f"{tool_name} {' '.join(tool_args)}"
            cache_entry = self.cache.lookup(source_file, tool_cmd)

        if cache_entry:
            # Cache hit - restore files (fast path!)
            if self.verbose:
                print(f"[Quicken] Cache HIT! Restoring cached output...", file=sys.stderr)

            # Restore to output_dir if provided, else original file's directory, else cpp_file directory
            restore_dir = output_dir if output_dir else (original_file.parent if original_file else cpp_file.parent)
            stdout, stderr, returncode = self.cache.restore(cache_entry, restore_dir)

            # Output stdout/stderr as if tool ran
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            return returncode
        else:
            # Cache miss - need to detect dependencies and run tool
            if self.verbose:
                print(f"[Quicken] Cache MISS. Finding dependencies...", file=sys.stderr)

            # Get local dependencies using /showIncludes (only on cache miss)
            local_files = self._get_local_dependencies(source_file, repo_dir)
            if self.verbose:
                print(f"[Quicken] Found {len(local_files)} local files", file=sys.stderr)

            # Run the tool
            if self.verbose:
                print(f"[Quicken] Running tool...", file=sys.stderr)

            # Use original file's directory as working directory if provided
            work_dir = original_file.parent if original_file else None
            output_files, stdout, stderr, returncode = self._run_tool(
                tool_name, modified_args, cpp_file, work_dir=work_dir, output_dir=output_dir
            )

            # Output stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            # Store in cache with dependency metadata
            if self.verbose:
                print(f"[Quicken] Storing results in cache...", file=sys.stderr)
            self.cache.store(source_file, tool_cmd, local_files, output_files, stdout, stderr, returncode)

            return returncode

    def run_repo_tool(self, repo_dir: Path, tool_name: str, tool_args: List[str],
                      main_file: Path, dependency_patterns: List[str],
                      output_dir: Path = None, optimization: Optional[int] = None) -> int:
        """
        Run a repo-level tool with caching based on dependency patterns.

        Args:
            repo_dir: Repository root directory
            tool_name: Tool to run (e.g., "doxygen")
            tool_args: Arguments for the tool
            main_file: Main file for the tool (e.g., Doxyfile path)
                       Used as cache index key
            dependency_patterns: Glob patterns for dependencies
                                 (e.g., ["*.cpp", "*.hpp", "*.h"])
            output_dir: Directory where tool creates output files
            optimization: Optimization level (0-3, or None to accept any cached level)

        Returns:
            Tool exit code
        """
        # Validate inputs
        if not dependency_patterns:
            raise ValueError("dependency_patterns cannot be empty for repo-level tools")

        if not main_file.exists():
            print(f"Error: Main file not found: {main_file}", file=sys.stderr)
            return 1

        if self.verbose:
            print(f"[Quicken] Processing repo with {tool_name}...", file=sys.stderr)

        # Use repo_dir as output directory if not specified
        if output_dir is None:
            output_dir = repo_dir

        # Step 1: Fast cache lookup (metadata comparison only)
        if self.verbose:
            print(f"[Quicken] Looking up in cache...", file=sys.stderr)

        cache_entry = None
        modified_args = None
        tool_cmd = None

        # Check if this tool supports optimization
        optimization_flags = self.config.get("optimization_flags", {})
        tool_supports_optimization = tool_name in optimization_flags

        if tool_supports_optimization:
            # If optimization is None, try to find cache hit with ANY optimization level
            if optimization is None:
                for opt_level in range(4):  # Try 0, 1, 2, 3
                    # Add optimization flag for this level
                    test_args = self._add_optimization_flag(tool_name, tool_args, opt_level)
                    # Build tool command string for cache key
                    test_cmd = f"{tool_name} {' '.join(test_args)}"
                    # Try lookup
                    cache_entry = self.cache.lookup(main_file, test_cmd)
                    if cache_entry:
                        if self.verbose:
                            print(f"[Quicken] Found cache hit with optimization level {opt_level}", file=sys.stderr)
                        modified_args = test_args
                        tool_cmd = test_cmd
                        break

                # If no cache hit found, default to O0 for execution
                if not cache_entry:
                    modified_args = self._add_optimization_flag(tool_name, tool_args, 0)
                    tool_cmd = f"{tool_name} {' '.join(modified_args)}"
            else:
                # Specific optimization level requested
                modified_args = self._add_optimization_flag(tool_name, tool_args, optimization)
                tool_cmd = f"{tool_name} {' '.join(modified_args)}"
                cache_entry = self.cache.lookup(main_file, tool_cmd)
        else:
            # Tool doesn't support optimization - use args as-is
            modified_args = tool_args
            tool_cmd = f"{tool_name} {' '.join(tool_args)}"
            cache_entry = self.cache.lookup(main_file, tool_cmd)

        if cache_entry:
            # Cache hit - restore files (fast path!)
            if self.verbose:
                print(f"[Quicken] Cache HIT! Restoring cached output...", file=sys.stderr)

            stdout, stderr, returncode = self.cache.restore(cache_entry, output_dir)

            # Output stdout/stderr as if tool ran
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            return returncode
        else:
            # Cache miss - need to detect dependencies and run tool
            if self.verbose:
                print(f"[Quicken] Cache MISS. Finding dependencies...", file=sys.stderr)

            # Get repo dependencies using glob patterns (only on cache miss)
            repo_files = self._get_repo_dependencies(repo_dir, dependency_patterns)
            if self.verbose:
                print(f"[Quicken] Found {len(repo_files)} files matching patterns", file=sys.stderr)

            # Run the tool
            if self.verbose:
                print(f"[Quicken] Running tool...", file=sys.stderr)

            output_files, stdout, stderr, returncode = self._run_repo_tool_impl(
                tool_name, modified_args, work_dir=repo_dir, output_dir=output_dir
            )

            # Output stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            # Only cache successful runs
            if returncode != 0:
                if self.verbose:
                    print(f"[Quicken] Tool failed (returncode={returncode}), not caching", file=sys.stderr)
                return returncode

            # Store in cache with dependency metadata
            if self.verbose:
                print(f"[Quicken] Storing results in cache...", file=sys.stderr)
            self.cache.store(
                main_file, tool_cmd, repo_files, output_files,
                stdout, stderr, returncode,
                repo_mode=True,
                dependency_patterns=dependency_patterns,
                output_base_dir=output_dir
            )

            return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
        if self.verbose:
            print(f"[Quicken] Cache cleared", file=sys.stderr)


def main():
    """CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Quicken - Cache wrapper for C++ build tools",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  quicken myfile.cpp cl /c /W4
  quicken myfile.cpp clang-tidy --checks=*
  quicken myfile.cpp clang++ -c -Wall
        """
    )

    parser.add_argument("cpp_file", nargs="?", type=Path, help="C++ source file to process")
    parser.add_argument("tool", nargs="?", help="Tool to run (must be in tools.json)")
    parser.add_argument("tool_args", nargs="*", help="Arguments to pass to the tool")
    parser.add_argument("--config", type=Path, default=Path("tools.json"),
                       help="Path to tools.json config file (default: ./tools.json)")
    parser.add_argument("--output-dir", type=Path,
                       help="Directory where tool creates output files (default: source file directory)")
    parser.add_argument("--optimization", "-O", type=int, choices=[0, 1, 2, 3],
                       help="Optimization level (0-3). If not specified, defaults to O0")
    parser.add_argument("--clear-cache", action="store_true",
                       help="Clear the entire cache and exit")

    args = parser.parse_args()

    try:
        quicken = Quicken(args.config)

        # Handle cache clearing
        if args.clear_cache:
            quicken.clear_cache()
            sys.exit(0)

        # Validate required arguments for normal operation
        if not args.cpp_file or not args.tool:
            parser.error("cpp_file and tool are required unless --clear-cache is used")

        returncode = quicken.run(args.cpp_file, args.tool, args.tool_args,
                                 output_dir=args.output_dir, optimization=args.optimization)
        sys.exit(returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
