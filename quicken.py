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
              output_files: List[Path], stdout: str, stderr: str, returncode: int) -> Path:
        """Store tool output in cache with dependency metadata."""
        source_key = str(source_file.resolve())

        # Generate unique cache key
        cache_key = f"entry_{self._next_id:06d}"
        self._next_id += 1

        cache_entry_dir = self.cache_dir / cache_key
        cache_entry_dir.mkdir(parents=True, exist_ok=True)

        # Store output files
        stored_files = []
        for output_file in output_files:
            if output_file.exists():
                dest = cache_entry_dir / output_file.name
                shutil.copy2(output_file, dest)
                stored_files.append(output_file.name)

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
            "returncode": returncode
        }

        with open(cache_entry_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        # Update index
        if source_key not in self.index:
            self.index[source_key] = []

        self.index[source_key].append({
            "cache_key": cache_key,
            "tool_cmd": tool_cmd,
            "dependencies": dep_metadata
        })
        self._save_index()

        return cache_entry_dir

    def restore(self, cache_entry_dir: Path, output_dir: Path) -> Tuple[str, str, int]:
        """Restore cached files to output directory."""
        metadata_file = cache_entry_dir / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        # Restore output files
        for filename in metadata["files"]:
            src = cache_entry_dir / filename
            dest = output_dir / filename
            if src.exists():
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
            output_dir: Path = None) -> int:
        """
        Main execution: optimized cache lookup, or get dependencies and run tool.

        Args:
            cpp_file: C++ file to process (may be a temp copy)
            tool_name: Tool to run
            tool_args: Arguments for the tool
            original_file: Original source file location (for dependency detection)
            repo_dir: Repository directory (for dependency filtering)
            output_dir: Directory where tool creates output files (for detection and cache restoration)
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

        # Build tool command string for cache key
        tool_cmd = f"{tool_name} {' '.join(tool_args)}"

        # Step 1: Fast cache lookup (no /showIncludes, just metadata comparison)
        if self.verbose:
            print(f"[Quicken] Looking up in cache...", file=sys.stderr)
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
                tool_name, tool_args, cpp_file, work_dir=work_dir, output_dir=output_dir
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
                                 output_dir=args.output_dir)
        sys.exit(returncode)
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
