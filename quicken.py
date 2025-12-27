"""
Quicken - A caching wrapper for C++ build tools

Quicken caches the output of C++ tools (compilers, analyzers like clang-tidy)
based on local file dependencies (using MSVC /showIncludes) and file hashes.
External libraries are ignored for caching to maximize speed.
"""

import time
import hashlib
import json
import logging
import os
import subprocess
import sys
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor


class RepoPath:
    """Stores a path to a file in the repo, relative to the repo. The file does not have to exist"""
    def __init__(self, path: Path):
        """Creates a relative path. Use this when you are sure that path is valid"""
        self.path = path

    @classmethod
    def fromAbsolutePath(cls, repo: Path, file_path: Path):
        """The repo and file_path need to be of type Path - not string, etc"""
        try:
            return RepoPath(file_path.relative_to(repo))
        except ValueError:
            return None # The requested file path was outside the repo

    @classmethod
    def fromRelativePath(cls, repo: Path, file_path: Path):
        """Create a RepoPath from a relative path. The repo and file_path need to be of type Path - not string, etc"""
        absolute_file_path = (repo / file_path).resolve() # Resolve handles any ../ etc.
        return cls.fromAbsolutePath(repo, absolute_file_path) # Validates the absolute path as the path may be outside the repo.

    def toAbsolutePath(self, repo: Path) -> Path:
        """Convert this repo-relative path to an absolute path.

        Args:
            repo: Repository root directory (should already be resolved for performance)

        Returns:
            Absolute path by joining repo with relative path
        """
        # Assume repo is already resolved for performance - just join paths
        return repo / self.path

    def __str__(self) -> str:
        """Return POSIX-style string representation for serialization.

        Uses forward slashes for cross-platform compatibility in JSON.
        """
        return self.path.as_posix()

    @classmethod
    def fromString(cls, path_str: str):
        """Create RepoPath from serialized string (trusted).

        Used when loading paths from cache - no validation needed.
        Returns RepoPath with path using forward slashes.
        """
        return RepoPath(Path(path_str))

    def calculateHash(self, repo: Path) -> str:
        """Calculate 64-bit hash of the file this path points to.

        Args:
            repo: Repository root to resolve relative path (should already be resolved)

        Returns:
            16-character hex string (64-bit BLAKE2b hash)
        """
        file_path = self.toAbsolutePath(repo)
        hash_obj = hashlib.blake2b(digest_size=8)  # 64-bit hash
        with open(file_path, 'rb') as f:
            # Read in chunks for efficiency with large files
            while chunk := f.read(8192):
                hash_obj.update(chunk)
        return hash_obj.hexdigest() 



class QuickenCache:
    """Manages caching of tool outputs based on source file and dependency metadata."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = cache_dir / "index.json"
        self.index = self._load_index()
        self._next_id = self._get_next_id()
        # Thread pool for async file restoration (max 4 concurrent copy operations)
        self._copy_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="quicken_copy")
        self._pending_copies = []  # Track pending copy futures

    def _load_index(self) -> Dict:
        """Load the cache index.

        Index structure (flat dictionary with compound keys):
        {
            "src/main.cpp::cl::['/c','/W4']": {
                "cache_key": "entry_001",
                "dependencies": [
                    {"path": "src/main.cpp", "hash": "a1b2c3d4e5f60708", "mtime_ns": ..., "size": ...},
                    {"path": "include/header.h", "hash": "b2c3d4e5f6071809", "mtime_ns": ..., "size": ...},
                    ...
                ]
            },
            ...
        }
        """
        if self.index_file.exists():
            try:
                with open(self.index_file, 'r') as f:
                    index = json.load(f)

                # Detect old format (values are lists instead of dicts)
                if index and isinstance(next(iter(index.values()), None), list):
                    # Old format detected - start fresh (no backwards compatibility)
                    return {}

                return index
            except json.JSONDecodeError:
                # Index file is empty or corrupted, start fresh
                return {}
        return {}

    def _save_index(self):
        """Save the cache index."""
        with open(self.index_file, 'w') as f:
            json.dump(self.index, f, indent=2)

    def _get_next_id(self) -> int:
        """Get next available cache entry ID."""
        max_id = 0
        for entry in self.index.values():
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

    def _make_cache_key(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str]) -> str:
        """Build compound cache key from source file, tool, and args.

        Args:
            source_repo_path: RepoPath for source file
            tool_name: Name of the tool
            tool_args: Tool arguments list

        Returns:
            Compound key string in format: "file::tool::args"
        """
        source_key = str(source_repo_path)
        args_str = json.dumps(tool_args, separators=(',', ':'))
        return f"{source_key}::{tool_name}::{args_str}"

    def _get_file_metadata(self, repo_path: RepoPath, repo_dir: Path) -> Dict:
        """Get metadata for a single file using repo-relative path.

        Args:
            repo_path: RepoPath instance for the file
            repo_dir: Repository root for hash calculation (should already be resolved)

        Returns:
            Dict with path, hash, mtime_ns (nanosecond precision), and size
        """
        file_path = repo_path.toAbsolutePath(repo_dir)
        stat = file_path.stat()
        return {
            "path": str(repo_path),  # Uses RepoPath.__str__() for POSIX format
            "hash": repo_path.calculateHash(repo_dir),
            "mtime_ns": stat.st_mtime_ns,  # Nanosecond precision timestamp
            "size": stat.st_size
        }

    def _dependencies_match(self, cached_deps: List[Dict], repo_dir: Path) -> Tuple[bool, bool]:
        """Check if cached dependencies match current file hashes.

        Fast path: Check mtime_ns+size first, only hash if mtime_ns changed.

        Args:
            cached_deps: List of dicts with 'path', 'hash', 'mtime_ns', 'size' keys
            repo_dir: Repository root (should already be resolved)

        Returns:
            Tuple of (matches, needs_update) where:
            - matches: True if all dependencies match
            - needs_update: True if mtime_ns was updated in cached_deps
        """
        needs_update = False

        for dep in cached_deps:
            dep_path_str = dep["path"]
            expected_hash = dep["hash"]

            # Load path from cache (trusted)
            repo_path = RepoPath.fromString(dep_path_str)

            # Convert to absolute path and check if file exists
            file_path = repo_path.toAbsolutePath(repo_dir)

            if not file_path.is_file():
                return False, False

            # Get current file stats
            stat = file_path.stat()
            current_mtime_ns = stat.st_mtime_ns  # Nanosecond precision
            current_size = stat.st_size

            cached_mtime_ns = dep.get("mtime_ns")
            cached_size = dep.get("size")

            # Fast path: mtime_ns and size unchanged - skip hashing
            if cached_mtime_ns is not None and cached_size is not None:
                if current_mtime_ns == cached_mtime_ns and current_size == cached_size:
                    continue

                # Size changed - file is definitely different
                if current_size != cached_size:
                    return False, False

            # Only mtime changed (or no cached mtime/size) - need to hash
            current_hash = repo_path.calculateHash(repo_dir)
            if current_hash != expected_hash:
                return False, False

            # Hash matches but mtime changed - update cache with new mtime_ns
            if cached_mtime_ns != current_mtime_ns or cached_size != current_size:
                dep["mtime_ns"] = current_mtime_ns
                dep["size"] = current_size
                needs_update = True

        return True, needs_update

    def lookup(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str],
               repo_dir: Path) -> Optional[Path]:
        """Look up cached output for given source file and tool command.

        This is the optimized fast path that doesn't run /showIncludes.
        It only checks file hashes against cached values.

        Args:
            source_repo_path: RepoPath for source file
            tool_name: Name of the tool
            tool_args: Tool arguments list
            repo_dir: Repository root (should already be resolved)

        Returns:
            Cache entry directory path if found, None otherwise
        """
        # Build compound key for O(1) lookup
        compound_key = self._make_cache_key(source_repo_path, tool_name, tool_args)

        if compound_key not in self.index:
            return None

        entry = self.index[compound_key]

        # For repo mode, verify main_file content hasn't changed
        if entry.get("repo_mode", False) and "main_file_hash" in entry:
            if source_repo_path.calculateHash(repo_dir) != entry["main_file_hash"]:
                return None

        matches, needs_update = self._dependencies_match(entry["dependencies"], repo_dir)
        if not matches:
            return None

        cache_entry_dir = self.cache_dir / entry["cache_key"]
        if not cache_entry_dir.exists():
            return None

        # Update metadata if mtime changed but hash matched
        if needs_update:
            # Update metadata.json in cache entry
            metadata_file = cache_entry_dir / "metadata.json"
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            metadata["dependencies"] = entry["dependencies"]
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            # Save updated index
            self._save_index()

        return cache_entry_dir

    def store(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str],
              dependency_repo_paths: List[RepoPath], output_files: List[Path],
              stdout: str, stderr: str, returncode: int,
              repo_dir: Path, repo_mode: bool = False,
              dependency_patterns: List[str] = None,
              output_base_dir: Path = None) -> Path:
        """Store tool output in cache with dependency hashes.

        Args:
            source_repo_path: RepoPath for source file (or main file for repo mode)
            tool_name: Name of the tool
            tool_args: Tool arguments (without main_file path)
            dependency_repo_paths: List of RepoPath instances for dependencies
            output_files: List of output file paths
            stdout: Tool stdout
            stderr: Tool stderr
            returncode: Tool exit code
            repo_dir: Repository directory (for hashing dependencies, should already be resolved)
            repo_mode: If True, this is a repo-level cache entry
            dependency_patterns: Glob patterns used (for repo mode)
            output_base_dir: Base directory for preserving relative paths

        Returns:
            Path to cache entry directory
        """
        source_key = str(source_repo_path)  # repo-relative path

        cache_key = f"entry_{self._next_id:06d}"
        self._next_id += 1

        cache_entry_dir = self.cache_dir / cache_key
        cache_entry_dir.mkdir(parents=True, exist_ok=True)

        stored_files = []
        for output_file in output_files:
            if output_file.exists():
                if output_base_dir:
                    try:
                        rel_path = output_file.relative_to(output_base_dir)
                        dest = cache_entry_dir / rel_path
                        file_path_str = str(rel_path)
                    except ValueError:
                        dest = cache_entry_dir / output_file.name
                        file_path_str = output_file.name
                else:
                    dest = cache_entry_dir / output_file.name
                    file_path_str = output_file.name

                dest.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(output_file, dest)
                stored_files.append(file_path_str)

        dep_metadata = [self._get_file_metadata(dep, repo_dir) for dep in dependency_repo_paths]

        metadata = {
            "cache_key": cache_key,
            "source_file": source_key,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "main_file_path": source_key,  # repo-relative path
            "dependencies": dep_metadata,
            "files": stored_files,
            "stdout": stdout,
            "stderr": stderr,
            "returncode": returncode
        }

        if repo_mode:
            metadata["repo_mode"] = True
            metadata["main_file_hash"] = source_repo_path.calculateHash(repo_dir)
            if dependency_patterns:
                metadata["dependency_patterns"] = dependency_patterns

        with open(cache_entry_dir / "metadata.json", 'w') as f:
            json.dump(metadata, f, indent=2)

        # Build compound key for flat dictionary
        compound_key = self._make_cache_key(source_repo_path, tool_name, tool_args)

        # Create minimized index entry (no redundant fields)
        index_entry = {
            "cache_key": cache_key,
            "dependencies": dep_metadata
        }

        if repo_mode:
            index_entry["repo_mode"] = True
            index_entry["main_file_hash"] = source_repo_path.calculateHash(repo_dir)
            if dependency_patterns:
                index_entry["dependency_patterns"] = dependency_patterns

        # Store directly at compound key (flat dictionary, not nested list)
        self.index[compound_key] = index_entry
        self._save_index()

        return cache_entry_dir

    def _copy_files(self, cache_entry_dir: Path, output_dir: Path, files ):
        """Worker method to copy files in background thread.

        Args:
            cache_entry_dir: Cache entry directory containing source files
            output_dir: Destination directory for restored files
            files: List of file names 
        """
        for file_path_str in files:
            # file_path_str may be relative path (e.g., "xml/index.xml")
            src = cache_entry_dir / file_path_str
            dest = output_dir / file_path_str

            # Create parent directories if needed
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(src, dest)

    def restore(self, cache_entry_dir: Path, output_dir: Path) -> Tuple[str, str, int]:
        """Restore cached files to output directory (async).

        Files are copied in a background thread pool, allowing this method
        to return quickly. Files will appear in output_dir within a few ms.

        Handles both flat files and directory trees using relative paths.

        Returns:
            Tuple of (stdout, stderr, returncode)
        """
        metadata_file = cache_entry_dir / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        files = metadata["files"]
 
        # Submit file copy job to thread pool (non-blocking) and track it
        future = self._copy_executor.submit(self._copy_files, cache_entry_dir, output_dir, files)
        self._pending_copies.append(future)

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

    def flush(self, timeout: float = 5.0):
        """Wait for all pending copy operations to complete.

        This ensures all files have been restored before continuing.
        The thread pool remains active for future operations.

        Args:
            timeout: Maximum seconds to wait for each copy (None = wait forever)
        """
        from concurrent.futures import wait, FIRST_EXCEPTION

        if self._pending_copies:
            # Wait for all pending copies to complete
            wait(self._pending_copies, timeout=timeout, return_when=FIRST_EXCEPTION)
            # Clear completed futures
            self._pending_copies = [f for f in self._pending_copies if not f.done()]


class ToolCmd(ABC):
    """Base class for tool command wrappers."""

    # Class attributes (overridden by subclasses)
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

    def __init__(self, tool_path: str, arguments: List[str], logger, config, cache, env, optimization=None):
        self.tool_path = tool_path
        self.arguments = arguments
        self.optimization = optimization
        self.config = config
        self.logger = logger
        self.cache = cache
        self.env = env  # Environment dict or None

    @staticmethod
    def get_tool_path(config: Dict, tool_name: str) -> str:
        """Get the full path to a tool from config.

        Args:
            config: Configuration dictionary
            tool_name: Name of the tool

        Returns:
            Full path to the tool executable
        """
        return config[tool_name]

    def get_optimization_flags(self, level: int) -> List[str]:
        """Return optimization flags for the given level.

        Args:
            level: Optimization level (0-3)

        Returns:
            List of flags (may be empty list, or multiple flags for space-separated)
        """
        if not self.supports_optimization:
            return []

        if level < 0 or level >= len(self.optimization_flags):
            raise ValueError(f"Invalid optimization level {level}")

        flag = self.optimization_flags[level]

        # Handle space-separated flags (e.g., "-O0 -fno-inline")
        if isinstance(flag, str) and ' ' in flag:
            return flag.split()

        return [flag] if isinstance(flag, str) else flag

    def add_optimization_flags(self, args: List[str]) -> List[str]:
        """Add optimization flags to arguments if optimization is set.

        Args:
            args: Original arguments

        Returns:
            Modified arguments with optimization flags at beginning
        """
        if not self.supports_optimization:
            return args

        # Default to O0 if not specified
        opt_level = self.optimization if self.optimization is not None else 0
        opt_flags = self.get_optimization_flags(opt_level)

        return opt_flags + args

    def build_execution_command(self, main_file: Path = None) -> List[str]:
        """Build complete command for execution.

        Args:
            main_file: Main file path for repo-level tools (e.g., Doxyfile) or source file for file-level tools

        Returns:
            Complete command list for subprocess
        """
        modified_args = self.add_optimization_flags(self.arguments)
        cmd = [self.tool_path] + modified_args

        if main_file:
            cmd.append(str(main_file))

        return cmd

    def try_all_optimization_levels(self, tool_name: str, tool_args: List[str],
                                   source_repo_path: RepoPath, repo_dir: Path) -> Tuple[Optional[Path], List[str]]:
        """Try to find cache hit with any optimization level.

        Args:
            tool_name: Name of the tool
            tool_args: Tool arguments (without source_file/main_file)
            source_repo_path: RepoPath for source file
            repo_dir: Repository directory (should already be resolved)

        Returns:
            Tuple of (cache_entry, modified_args) or (None, original_args)
        """
        if not self.supports_optimization:
            cache_entry = self.cache.lookup(source_repo_path, tool_name, tool_args, repo_dir)
            return cache_entry, tool_args

        for opt_level in range(len(self.optimization_flags)):
            self.optimization = opt_level
            modified_args = self.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, repo_dir)
            if cache_entry:
                return cache_entry, modified_args

        # No cache hit found - default to optimization level 0
        self.optimization = 0
        modified_args = self.add_optimization_flags(tool_args)
        return None, modified_args


class ClCmd(ToolCmd):
    supports_optimization = True
    optimization_flags = ["/Od", "/O1", "/O2", "/Ox"]
    needs_vcvars = True

class ClangCmd(ToolCmd):
    supports_optimization = True
    optimization_flags = ["-O0", "-O1", "-O2", "-O3"]
    needs_vcvars = False

class ClangTidyCmd(ToolCmd):
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

class DoxygenCmd(ToolCmd):
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

class ToolRegistry:
    """Registry for tool command classes."""

    _registry = {
        "cl": ClCmd,
        "clang++": ClangCmd,
        "clang-tidy": ClangTidyCmd,
        "doxygen": DoxygenCmd,
    }

    @classmethod
    def create(cls, tool_name: str, tool_path: str, arguments: List[str],
               logger, config, cache, quicken, optimization=None) -> ToolCmd:
        """Create ToolCmd instance for the given tool name.

        Args:
            tool_name: Name of the tool (must be registered)
            tool_path: Full path to tool executable
            arguments: Command-line arguments
            logger: Logger instance
            config: Configuration dict
            cache: QuickenCache instance
            quicken: Quicken instance (for environment access if needed)
            optimization: Optional optimization level

        Returns:
            ToolCmd subclass instance

        Raises:
            ValueError: If tool_name is not registered
        """
        if tool_name not in cls._registry:
            raise ValueError(f"Unsupported tool: {tool_name}")

        tool_class = cls._registry[tool_name]

        # Get environment if tool needs vcvars
        env = quicken._msvc_env if tool_class.needs_vcvars else None

        return tool_class(tool_path, arguments, logger, config, cache,
                         env, optimization)

class Quicken:
    """Main Quicken application."""

    def __init__(self, config_path: Path):
        self.config = self._load_config(config_path)
        self.cache = QuickenCache(Path.home() / ".quicken" / "cache")
        self._setup_logging()
        # Eagerly fetch and cache MSVC environment (assumes MSVC is installed)
        self._msvc_env = self._get_msvc_environment()

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

    def _get_msvc_environment(self) -> Dict:
        """Get MSVC environment variables, cached to avoid repeated vcvarsall.bat calls."""
        vcvarsall = ToolCmd.get_tool_path(self.config, "vcvarsall")
        msvc_arch = self.config.get("msvc_arch", "x64")

        # Cache file location
        cache_file = Path.home() / ".quicken" / "msvc_env.json"

        # Try to load from cache
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                    # Verify cache is for same vcvarsall and arch
                    if (cached_data.get("vcvarsall") == vcvarsall and
                        cached_data.get("msvc_arch") == msvc_arch):
                        return cached_data.get("env", {})
            except (json.JSONDecodeError, KeyError):
                # Cache corrupted, will regenerate
                pass

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

        # Save to cache
        cache_data = {
            "vcvarsall": vcvarsall,
            "msvc_arch": msvc_arch,
            "env": env
        }

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            # If caching fails, still return the environment
            pass

        return env

    def _get_local_dependencies(self, source_file: Path, repo_dir: Path) -> List[Path]:
        """Get list of local (repo) file dependencies using MSVC /showIncludes.

        Args:
            source_file: Source file to analyze
            repo_dir: Repository directory (should already be resolved)
        """
        cl_path = ToolCmd.get_tool_path(self.config, "cl")

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        # This is much faster than full preprocessing
        result = subprocess.run(
            [cl_path, '/showIncludes', '/Zs', str(source_file)],
            env=self._msvc_env,
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
                    file_path.resolve().relative_to(repo_dir)
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

    def _run_repo_tool_impl(self, tool: ToolCmd, tool_args: List[str],
                            main_file: Path, work_dir: Path, output_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run repo-level tool and detect all output files.

        Args:
            tool: ToolCmd instance
            tool_args: Arguments to pass to tool (already includes optimization)
            main_file: Main file path (e.g., Doxyfile)
            work_dir: Working directory for tool execution
            output_dir: Directory where tool creates output files

        Returns:
            Tuple of (output_files, stdout, stderr, returncode)
        """
        files_before = set(output_dir.rglob("*")) if output_dir.exists() else set()

        cmd = tool.build_execution_command(main_file)

        result = subprocess.run(
            cmd,
            cwd=work_dir,
            capture_output=True,
            text=True,
            env=tool.env
        )

        files_after = set(output_dir.rglob("*")) if output_dir.exists() else set()
        new_files = files_after - files_before
        output_files = [f for f in new_files if f.is_file()]

        return output_files, result.stdout, result.stderr, result.returncode

    def _run_tool(self, tool: ToolCmd, tool_args: List[str], source_file: Path,
                  work_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run the specified tool with arguments.

        Args:
            tool: ToolCmd instance
            tool_args: Arguments to pass to tool (already includes optimization flags)
            source_file: Path to C++ file to process
            work_dir: Directory to look for output files

        Returns:
            Tuple of (output_files, stdout, stderr, returncode)
        """
        files_before = set(work_dir.iterdir()) if work_dir.exists() else set()

        cmd = tool.build_execution_command(source_file)

        result = subprocess.run(
            cmd,
            cwd=source_file.parent,
            capture_output=True,
            text=True,
            env=tool.env
        )

        files_after = set(work_dir.iterdir()) if work_dir.exists() else set()
        new_files = files_after - files_before
        output_files = [f for f in new_files if f.is_file() and f != source_file]

        return output_files, result.stdout, result.stderr, result.returncode

    def run(self, source_file: Path, tool_name: str, tool_args: List[str],
            repo_dir: Path, output_dir: Path, optimization: int = None) -> int:
        """
        Main execution: optimized cache lookup, or get dependencies and run tool.

        Args:
            source_file: C++ file to process (absolute or relative path)
            tool_name: Tool to run
            tool_args: Arguments for the tool
            repo_dir: Repository directory (for dependency filtering)
            output_dir: Directory where tool creates output files (for detection and cache restoration)
            optimization: Optimization level (0-3, or None to accept any cached level)

        Returns:
            Tool exit code (integer)
        """
        # OPTIMIZATION: Resolve repo_dir once at entry point to avoid repeated resolve() calls
        repo_dir = repo_dir.resolve()

        # VALIDATE: Convert source_file to RepoPath at API entry
        if source_file.is_absolute():
            source_repo_path = RepoPath.fromAbsolutePath(repo_dir, source_file)
        else:
            source_repo_path = RepoPath.fromRelativePath(repo_dir, source_file)

        if source_repo_path is None:
            raise ValueError(f"Source file {source_file} is outside repository {repo_dir}")

        # VALIDATE: output_dir must be within repo
        if output_dir.is_absolute():
            output_repo_path = RepoPath.fromAbsolutePath(repo_dir, output_dir)
        else:
            output_repo_path = RepoPath.fromRelativePath(repo_dir, output_dir)

        if output_repo_path is None:
            raise ValueError(f"Output directory {output_dir} is outside repository {repo_dir}")

        # Convert RepoPath back to absolute paths for tool execution
        abs_source_file = source_repo_path.toAbsolutePath(repo_dir)
        abs_output_dir = output_repo_path.toAbsolutePath(repo_dir)

        start_time = time.perf_counter()
        tool_path = ToolCmd.get_tool_path(self.config, tool_name)
        tool = ToolRegistry.create(
            tool_name, tool_path, tool_args,
            self.logger, self.config, self.cache, self, optimization
        )

        if optimization is None:
            cache_entry, modified_args = tool.try_all_optimization_levels(
                tool_name, tool_args, source_repo_path, repo_dir
            )
        else:
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, repo_dir)

        if cache_entry:
            stdout, stderr, returncode = self.cache.restore(cache_entry, abs_output_dir)
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)
            self.logger.info(f"CACHE HIT - source_file: {source_repo_path}, tool: {tool_name}, "
                           f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                           f"args: {modified_args}, cache_entry: {cache_entry.name}, "
                           f"returncode: {returncode}")
            # Flush to ensure all async file copies are complete
            self.cache.flush()
            return returncode

        local_files = self._get_local_dependencies(abs_source_file, repo_dir)

        # Convert absolute dependency paths to RepoPath
        local_dep_repo_paths = []
        for dep_path in local_files:
            dep_repo_path = RepoPath.fromAbsolutePath(repo_dir, dep_path)
            if dep_repo_path is not None:  # Should always succeed for local deps
                local_dep_repo_paths.append(dep_repo_path)

        output_files, stdout, stderr, returncode = self._run_tool(
            tool, modified_args, abs_source_file, abs_output_dir
        )


        if stdout:
            print(stdout, end='')
        if stderr:
            print(stderr, end='', file=sys.stderr)

        self.cache.store(
            source_repo_path, tool_name, modified_args, local_dep_repo_paths, output_files,
            stdout, stderr, returncode, repo_dir,
            repo_mode=False,
            output_base_dir=abs_output_dir
        )
        self.logger.info(f"CACHE MISS - source_file: {source_repo_path}, tool: {tool_name}, "
                       f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                       f"args: {modified_args}, dependencies: {len(local_dep_repo_paths)}, "
                       f"returncode: {returncode}, output_files: {len(output_files)}")

        # Flush to ensure all async file copies are complete
        self.cache.flush()

        return returncode

    def run_repo_tool(self, repo_dir: Path, tool_name: str, tool_args: List[str],
                      main_file: Path, dependency_patterns: List[str],
                      output_dir: Path, optimization: int = None) -> int:
        """
        Run a repo-level tool with caching based on dependency patterns.

        Args:
            repo_dir: Repository root directory
            tool_name: Tool to run (e.g., "doxygen")
            tool_args: Arguments for the tool (WITHOUT main_file path)
            main_file: Main file for the tool (e.g., Doxyfile path - absolute or relative)
            dependency_patterns: Glob patterns for dependencies
            output_dir: Directory where tool creates output files (absolute or relative)
            optimization: Optimization level (0-3, or None to accept any cached level)

        Returns:
            Tool exit code (integer)
        """
        # OPTIMIZATION: Resolve repo_dir once at entry point to avoid repeated resolve() calls
        repo_dir = repo_dir.resolve()

        # VALIDATE: Convert main_file to RepoPath at API entry
        if main_file.is_absolute():
            main_repo_path = RepoPath.fromAbsolutePath(repo_dir, main_file)
        else:
            main_repo_path = RepoPath.fromRelativePath(repo_dir, main_file)

        if main_repo_path is None:
            raise ValueError(f"Main file {main_file} is outside repository {repo_dir}")

        # VALIDATE: output_dir must be within repo
        if output_dir.is_absolute():
            output_repo_path = RepoPath.fromAbsolutePath(repo_dir, output_dir)
        else:
            output_repo_path = RepoPath.fromRelativePath(repo_dir, output_dir)

        if output_repo_path is None:
            raise ValueError(f"Output directory {output_dir} is outside repository {repo_dir}")

        # Convert RepoPath back to absolute paths for tool execution
        abs_main_file = main_repo_path.toAbsolutePath(repo_dir)
        abs_output_dir = output_repo_path.toAbsolutePath(repo_dir)

        start_time = time.perf_counter();

        tool_path = ToolCmd.get_tool_path(self.config, tool_name)
        tool = ToolRegistry.create(
            tool_name, tool_path, tool_args,
            self.logger, self.config, self.cache, self, optimization
        )

        if optimization is None:
            cache_entry, modified_args = tool.try_all_optimization_levels(
                tool_name, tool_args, main_repo_path, repo_dir
            )
        else:
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(main_repo_path, tool_name, modified_args, repo_dir)

        if cache_entry:
            stdout, stderr, returncode = self.cache.restore(cache_entry, abs_output_dir)
            self.logger.info(f"CACHE HIT (REPO) - repo_dir: {repo_dir}, tool: {tool_name}, "
                           f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                           f"args: {modified_args}, main_file: {main_repo_path}, "
                           f"cache_entry: {cache_entry.name}, returncode: {returncode}")
            if stdout:
                print(stdout, end='')
            if stderr:
                print(stderr, end='', file=sys.stderr)
            # Flush to ensure all async file copies are complete
            self.cache.flush()
            return returncode

        repo_files = self._get_repo_dependencies(repo_dir, dependency_patterns)

        # Convert absolute dependency paths to RepoPath
        repo_dep_repo_paths = []
        for dep_path in repo_files:
            dep_repo_path = RepoPath.fromAbsolutePath(repo_dir, dep_path)
            if dep_repo_path is not None:  # Should always succeed for repo deps
                repo_dep_repo_paths.append(dep_repo_path)

        output_files, stdout, stderr, returncode = self._run_repo_tool_impl(
            tool, modified_args, abs_main_file, work_dir=repo_dir, output_dir=abs_output_dir
        )

        if stdout:
            print(stdout, end='')
        if stderr:
            print(stderr, end='', file=sys.stderr)

        if returncode == 0:
            self.cache.store(
                main_repo_path, tool_name, modified_args, repo_dep_repo_paths, output_files,
                stdout, stderr, returncode, repo_dir,
                repo_mode=True,
                dependency_patterns=dependency_patterns,
                output_base_dir=abs_output_dir
            )

        self.logger.info(f"CACHE MISS (REPO) - repo_dir: {repo_dir}, tool: {tool_name}, "
                       f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                       f"args: {modified_args}, main_file: {main_repo_path}, "
                       f"dependencies: {len(repo_dep_repo_paths)}, returncode: {returncode}, "
                       f"output_files: {len(output_files)}")

        # Flush to ensure all async file copies are complete
        self.cache.flush()

        return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()

    def flush(self, timeout: float = 5.0):
        """Wait for all pending copy operations to complete.

        Call this to ensure all async file copies have finished before
        accessing output files or exiting the application.

        Args:
            timeout: Maximum seconds to wait for copies (None = wait forever)
        """
        self.cache.flush(timeout=timeout)
