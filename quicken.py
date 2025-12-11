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
    """Manages caching of tool outputs based on TU hash and command."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = cache_dir / "index.json"
        self.index = self._load_index()

    def _load_index(self) -> Dict:
        """Load the cache index."""
        if self.index_file.exists():
            with open(self.index_file, 'r') as f:
                return json.load(f)
        return {}

    def _save_index(self):
        """Save the cache index."""
        with open(self.index_file, 'w') as f:
            json.dump(self.index, f, indent=2)

    def _get_cache_key(self, tu_hash: str, tool_cmd: str) -> str:
        """Generate a cache key from TU hash and tool command."""
        cmd_hash = hashlib.sha256(tool_cmd.encode()).hexdigest()[:16]
        return f"{tu_hash}_{cmd_hash}"

    def lookup(self, tu_hash: str, tool_cmd: str) -> Optional[Path]:
        """Look up cached output for given TU hash and tool command."""
        cache_key = self._get_cache_key(tu_hash, tool_cmd)
        if cache_key in self.index:
            cache_entry_dir = self.cache_dir / cache_key
            if cache_entry_dir.exists():
                return cache_entry_dir
        return None

    def store(self, tu_hash: str, tool_cmd: str, output_files: List[Path],
              stdout: str, stderr: str, returncode: int) -> Path:
        """Store tool output in cache."""
        cache_key = self._get_cache_key(tu_hash, tool_cmd)
        cache_entry_dir = self.cache_dir / cache_key
        cache_entry_dir.mkdir(parents=True, exist_ok=True)

        # Store output files
        stored_files = []
        for output_file in output_files:
            if output_file.exists():
                dest = cache_entry_dir / output_file.name
                shutil.copy2(output_file, dest)
                stored_files.append(output_file.name)

        # Store metadata
        metadata = {
            "tu_hash": tu_hash,
            "tool_cmd": tool_cmd,
            "files": stored_files,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode
        }

        with open(cache_entry_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        self.index[cache_key] = {
            "tu_hash": tu_hash,
            "tool_cmd": tool_cmd,
            "files": stored_files
        }
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

    def _get_local_dependencies(self, cpp_file: Path, repo_dir: Path) -> List[Path]:
        """Get list of local (repo) file dependencies using MSVC /showIncludes."""
        cl_path = self._get_tool_path("cl")
        vcvarsall = self._get_tool_path("vcvarsall")
        msvc_arch = self.config.get("msvc_arch", "x64")

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        # This is much faster than full preprocessing
        cmd = f'"{vcvarsall}" {msvc_arch} >nul && "{cl_path}" /showIncludes /Zs "{cpp_file}"'

        try:
            result = subprocess.run(
                cmd,
                shell=True,
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
                        file_path.resolve().relative_to(repo_dir.resolve())
                        dependencies.append(file_path)
                    except ValueError:
                        # File is outside repo, skip it
                        pass

            return dependencies
        except Exception as e:
            raise RuntimeError(f"Failed to get dependencies: {e}")

    def _hash_file_metadata(self, file_paths: List[Path]) -> str:
        """Hash file metadata (path, size, mtime) instead of contents for speed."""
        # Using BLAKE2b for speed
        hasher = hashlib.blake2b(digest_size=32)

        # Sort paths for consistent ordering
        sorted_paths = sorted(file_paths, key=lambda p: str(p))

        for file_path in sorted_paths:
            if file_path.exists():
                stat = file_path.stat()
                # Hash: normalized path, size, and modification time
                metadata = f"{file_path.resolve()}|{stat.st_size}|{stat.st_mtime_ns}"
                hasher.update(metadata.encode('utf-8'))

        return hasher.hexdigest()

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
            vcvarsall = self._get_tool_path("vcvarsall")
            msvc_arch = self.config.get("msvc_arch", "x64")
            cmd_str = ' '.join(f'"{arg}"' if ' ' in str(arg) else str(arg) for arg in cmd)
            full_cmd = f'"{vcvarsall}" {msvc_arch} >nul && {cmd_str}'

            result = subprocess.run(
                full_cmd,
                shell=True,
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
        Main execution: get dependencies, hash metadata, lookup cache, or run tool.

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

        # Use original file for dependency detection, or cpp_file if not provided
        dependency_file = original_file if original_file else cpp_file

        # Determine repository directory
        if not repo_dir:
            repo_dir = Path.cwd()

        # Step 1: Get local dependencies using /showIncludes
        if self.verbose:
            print(f"[Quicken] Finding local dependencies...", file=sys.stderr)
        local_files = self._get_local_dependencies(dependency_file, repo_dir)
        if self.verbose:
            print(f"[Quicken] Found {len(local_files)} local files", file=sys.stderr)

        # Step 2: Hash file metadata (size + mtime)
        if self.verbose:
            print(f"[Quicken] Hashing file metadata...", file=sys.stderr)
        files_hash = self._hash_file_metadata(local_files)
        if self.verbose:
            print(f"[Quicken] Files Hash: {files_hash}", file=sys.stderr)

        # Build tool command string for cache key
        tool_cmd = f"{tool_name} {' '.join(tool_args)}"

        # Step 3: Lookup in cache
        if self.verbose:
            print(f"[Quicken] Looking up in cache...", file=sys.stderr)
        cache_entry = self.cache.lookup(files_hash, tool_cmd)

        if cache_entry:
            # Step 4a: Cache hit - restore files
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
            # Step 4b: Cache miss - run tool
            if self.verbose:
                print(f"[Quicken] Cache MISS. Running tool...", file=sys.stderr)

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

            # Store in cache
            if self.verbose:
                print(f"[Quicken] Storing results in cache...", file=sys.stderr)
            self.cache.store(files_hash, tool_cmd, output_files, stdout, stderr, returncode)

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
