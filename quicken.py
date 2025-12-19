"""
Quicken - A caching wrapper for C++ build tools

Quicken caches the output of C++ tools (compilers, analyzers like clang-tidy)
based on local file dependencies (using MSVC /showIncludes) and file hashes.
External libraries are ignored for caching to maximize speed.
"""

import hashlib
import json
import logging
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
            "source_filename": [
                {
                    "cache_key": "entry_001",
                    "tool_cmd": "cl /c /W4",
                    "dependencies": [
                        {"name": "file.cpp", "hash": "a1b2c3d4e5f60708"},
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
                    entry_id = int(cache_key.split("_")[1])
                    max_id = max(max_id, entry_id)
        return max_id + 1

    def _get_file_hash(self, file_path: Path) -> str:
        """Calculate 64-bit hash of file content.

        Returns 16-character hex string for human readability in JSON.
        """
        hash_obj = hashlib.blake2b(digest_size=8)  # 64-bit hash
        with open(file_path, 'rb') as f:
            # Read in chunks for efficiency with large files
            while chunk := f.read(8192):
                hash_obj.update(chunk)
        return hash_obj.hexdigest()

    def _get_file_metadata(self, file_path: Path, repo_dir: Path) -> Dict:
        """Get metadata for a single file.

        Stores only filename (not path) for portability across different
        directories, including temporary directories.
        """
        return {
            "name": file_path.name,
            "hash": self._get_file_hash(file_path)
        }

    def _dependencies_match(self, cached_deps: List[Dict], repo_dir: Path) -> bool:
        """Check if all cached dependencies still match their file hashes.

        Searches repo_dir for files with matching names and verifies their hashes.
        This allows cache hits even when files are in different directories
        (e.g., different temporary directories).
        """
        for dep in cached_deps:
            dep_name = dep["name"]
            expected_hash = dep["hash"]

            # Search for file with this name in repo_dir
            matching_files = list(repo_dir.rglob(dep_name))

            if not matching_files:
                return False

            # Check if any matching file has the correct hash
            found_match = False
            for file_path in matching_files:
                if file_path.is_file():
                    current_hash = self._get_file_hash(file_path)
                    if current_hash == expected_hash:
                        found_match = True
                        break

            if not found_match:
                return False

        return True

    def lookup(self, source_file: Path, tool_cmd: str, repo_dir: Path) -> Optional[Path]:
        """Look up cached output for given source file and tool command.

        This is the optimized fast path that doesn't run /showIncludes.
        It only checks file hashes against cached values.

        Uses filename (not path) for portability across different locations.
        """
        # Use filename only as index key (not path)
        source_key = source_file.name

        if source_key not in self.index:
            return None

        # Check each cached entry for this source file
        for entry in self.index[source_key]:
            # Check if tool command matches
            if entry["tool_cmd"] != tool_cmd:
                continue

            # Check if all dependencies still match their cached metadata
            if not self._dependencies_match(entry["dependencies"], repo_dir):
                continue

            # Cache hit! Return the cache entry directory
            cache_entry_dir = self.cache_dir / entry["cache_key"]
            if cache_entry_dir.exists():
                return cache_entry_dir

        return None

    def store(self, source_file: Path, tool_cmd: str, dependencies: List[Path],
              output_files: List[Path], stdout: str, stderr: str, returncode: int,
              repo_dir: Path, repo_mode: bool = False, dependency_patterns: List[str] = None,
              output_base_dir: Path = None) -> Path:
        """Store tool output in cache with dependency hashes.

        Args:
            source_file: Source file path (or main file for repo mode)
            tool_cmd: Tool command string
            dependencies: List of dependency file paths
            output_files: List of output file paths
            stdout: Tool stdout
            stderr: Tool stderr
            returncode: Tool exit code
            repo_dir: Repository directory (for hashing dependencies)
            repo_mode: If True, this is a repo-level cache entry
            dependency_patterns: Glob patterns used (for repo mode)
            output_base_dir: Base directory for preserving relative paths

        Returns:
            Path to cache entry directory
        """
        # Use filename only as index key (not path)
        source_key = source_file.name

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

        # Collect dependency hashes (with relative paths for portability)
        dep_metadata = [self._get_file_metadata(dep, repo_dir) for dep in dependencies]

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

        Returns:
            Tuple of (stdout, stderr, returncode)
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

    def __init__(self, config_path: Path):
        self.config = self._load_config(config_path)
        self.cache = QuickenCache(Path.home() / ".quicken" / "cache")
        self._msvc_env = None  # Cached MSVC environment
        self._setup_logging()

    def _load_config(self, config_path: Path) -> Dict:
        """Load tools configuration."""
        with open(config_path, 'r') as f:
            return json.load(f)

    def _setup_logging(self):
        """Set up logging to file."""
        log_dir = Path.home() / ".quicken"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "quicken.log"

        # Configure logger
        self.logger = logging.getLogger("Quicken")
        self.logger.setLevel(logging.INFO)

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # File handler
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.INFO)

        # Format: timestamp - level - message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)

    def _get_tool_path(self, tool_name: str) -> str:
        """Get the full path to a tool from config."""
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

        # Get flags for this tool
        opt_flag = optimization_flags[tool_name][opt_level]

        # Handle space-separated flags (e.g., "-O0 -fno-inline")
        if isinstance(opt_flag, str) and ' ' in opt_flag:
            opt_flags = opt_flag.split()
        else:
            opt_flags = [opt_flag]

        # Insert at beginning of args
        return opt_flags + tool_args

    def _get_local_dependencies(self, source_file: Path, repo_dir: Path) -> List[Path]:
        """Get list of local (repo) file dependencies using MSVC /showIncludes."""
        cl_path = self._get_tool_path("cl")
        env = self._get_msvc_environment()

        # Pre-resolve repo_dir once for faster filtering
        repo_dir_resolved = repo_dir.resolve()

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        # This is much faster than full preprocessing
        result = subprocess.run(
            [cl_path, '/showIncludes', '/Zs', str(source_file)],
            env=env,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse /showIncludes output
        # Format: "Note: including file:   <path>"
        dependencies = [source_file]  # Always include the source file itself

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

    def _run_tool(self, tool_name: str, tool_args: List[str], source_file: Path,
                  output_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run the specified tool with arguments.

        Args:
            tool_name: Name of tool to run
            tool_args: Arguments to pass to tool
            source_file: Path to C++ file to process
            output_dir: Directory to look for output files
        """
        tool_path = self._get_tool_path(tool_name)

        # Tool runs in source file's directory (for relative includes)
        work_dir = source_file.parent

        # Output directory (where to look for output files)
        output_directory = output_dir

        # Build command with just filename (we're in same directory)
        cmd = [tool_path] + tool_args + [source_file.name]

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

        # Detect output files by comparing directory contents before/after
        files_after = set(output_directory.iterdir()) if output_directory.exists() else set()
        new_files = files_after - files_before

        # Filter to only include actual output files (not directories, not source file)
        output_files = []
        for file_path in new_files:
            if file_path.is_file() and file_path != source_file:
                output_files.append(file_path)

        return output_files, result.stdout, result.stderr, result.returncode

    def run(self, source_file: Path, tool_name: str, tool_args: List[str],
            repo_dir: Path, output_dir: Path, optimization: int = None) -> int:
        """
        Main execution: optimized cache lookup, or get dependencies and run tool.

        Args:
            source_file: C++ file to process
            tool_name: Tool to run
            tool_args: Arguments for the tool
            repo_dir: Repository directory (for dependency filtering)
            output_dir: Directory where tool creates output files (for detection and cache restoration)
            optimization: Optimization level (0-3, or None to accept any cached level)

        Returns:
            Tool exit code (integer)
        """
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
                    cache_entry = self.cache.lookup(source_file, test_cmd, repo_dir)
                    if cache_entry:
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
                cache_entry = self.cache.lookup(source_file, tool_cmd, repo_dir)
        else:
            # Tool doesn't support optimization - use args as-is
            modified_args = tool_args
            tool_cmd = f"{tool_name} {' '.join(tool_args)}"
            cache_entry = self.cache.lookup(source_file, tool_cmd, repo_dir)

        if cache_entry:
            # Cache hit - restore files and print output (fast path!)
            stdout, stderr, returncode = self.cache.restore(cache_entry, output_dir)
            self.logger.info(f"CACHE HIT - file: {source_file}, tool: {tool_name}, "
                           f"args: {tool_args}, repo_dir: {repo_dir}, output_dir: {output_dir}, "
                           f"optimization: {optimization}, cache_entry: {cache_entry.name}, returncode: {returncode}")
            # Print to stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)
            return returncode
        else:
            # Cache miss - need to detect dependencies and run tool
            # Get local dependencies using /showIncludes (only on cache miss)
            local_files = self._get_local_dependencies(source_file, repo_dir)

            # Run the tool
            output_files, stdout, stderr, returncode = self._run_tool(
                tool_name, modified_args, source_file, output_dir=output_dir
            )

            self.logger.info(f"CACHE MISS - file: {source_file}, tool: {tool_name}, "
                           f"args: {tool_args}, repo_dir: {repo_dir}, output_dir: {output_dir}, "
                           f"optimization: {optimization}, dependencies: {len(local_files)}, "
                           f"returncode: {returncode}, output_files: {len(output_files)}")

            # Print to stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            # Store in cache with dependency hashes
            self.cache.store(source_file, tool_cmd, local_files, output_files, stdout, stderr, returncode, repo_dir)

            return returncode

    def run_repo_tool(self, repo_dir: Path, tool_name: str, tool_args: List[str],
                      main_file: Path, dependency_patterns: List[str],
                      output_dir: Path, optimization: int = None) -> int:
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
            Tool exit code (integer)
        """
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
                    cache_entry = self.cache.lookup(main_file, test_cmd, repo_dir)
                    if cache_entry:
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
                cache_entry = self.cache.lookup(main_file, tool_cmd, repo_dir)
        else:
            # Tool doesn't support optimization - use args as-is
            modified_args = tool_args
            tool_cmd = f"{tool_name} {' '.join(tool_args)}"
            cache_entry = self.cache.lookup(main_file, tool_cmd, repo_dir)

        if cache_entry:
            # Cache hit - restore files and print output (fast path!)
            stdout, stderr, returncode = self.cache.restore(cache_entry, output_dir)
            self.logger.info(f"CACHE HIT (REPO) - repo_dir: {repo_dir}, tool: {tool_name}, "
                           f"args: {tool_args}, main_file: {main_file}, patterns: {dependency_patterns}, "
                           f"output_dir: {output_dir}, optimization: {optimization}, "
                           f"cache_entry: {cache_entry.name}, returncode: {returncode}")
            # Print to stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)
            return returncode
        else:
            # Cache miss - need to detect dependencies and run tool
            # Get repo dependencies using glob patterns (only on cache miss)
            repo_files = self._get_repo_dependencies(repo_dir, dependency_patterns)

            # Run the tool
            output_files, stdout, stderr, returncode = self._run_repo_tool_impl(
                tool_name, modified_args, work_dir=repo_dir, output_dir=output_dir
            )

            self.logger.info(f"CACHE MISS (REPO) - repo_dir: {repo_dir}, tool: {tool_name}, "
                           f"args: {tool_args}, main_file: {main_file}, patterns: {dependency_patterns}, "
                           f"output_dir: {output_dir}, optimization: {optimization}, "
                           f"dependencies: {len(repo_files)}, returncode: {returncode}, "
                           f"output_files: {len(output_files)}, will_cache: {returncode == 0}")

            # Print to stdout/stderr
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)

            # Cache successful runs
            if returncode == 0:
                self.cache.store(
                    main_file, tool_cmd, repo_files, output_files,
                    stdout, stderr, returncode, repo_dir,
                    repo_mode=True,
                    dependency_patterns=dependency_patterns,
                    output_base_dir=output_dir
                )

            return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
