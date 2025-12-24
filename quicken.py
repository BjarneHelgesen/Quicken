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
            repo: Repository root directory

        Returns:
            Absolute path by joining repo with relative path
        """
        return (repo / self.path).resolve()

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
            repo: Repository root to resolve relative path

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

    def _load_index(self) -> Dict:
        """Load the cache index.

        Index structure:
        {
            "src/main.cpp": [
                {
                    "cache_key": "entry_001",
                    "tool_name": "cl",
                    "tool_args": ["/c", "/W4"],
                    "main_file_path": "src/main.cpp",
                    "dependencies": [
                        {"path": "src/main.cpp", "hash": "a1b2c3d4e5f60708"},
                        {"path": "include/header.h", "hash": "b2c3d4e5f6071809"},
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

    def _get_file_metadata(self, repo_path: RepoPath, repo_dir: Path) -> Dict:
        """Get metadata for a single file using repo-relative path.

        Args:
            repo_path: RepoPath instance for the file
            repo_dir: Repository root for hash calculation

        Returns:
            Dict with path (as string) and hash
        """
        return {
            "path": str(repo_path),  # Uses RepoPath.__str__() for POSIX format
            "hash": repo_path.calculateHash(repo_dir)
        }

    def _dependencies_match(self, cached_deps: List[Dict], repo_dir: Path) -> bool:
        """Check if cached dependencies match current file hashes.

        Uses direct path lookup with repo-relative paths.

        Args:
            cached_deps: List of dicts with 'path' and 'hash' keys
            repo_dir: Repository root

        Returns:
            True if all dependencies match, False otherwise
        """
        for dep in cached_deps:
            dep_path_str = dep["path"]
            expected_hash = dep["hash"]

            # Load path from cache (trusted)
            repo_path = RepoPath.fromString(dep_path_str)

            # Convert to absolute path and check if file exists
            file_path = repo_path.toAbsolutePath(repo_dir)

            if not file_path.is_file():
                return False

            # Calculate current hash and compare
            current_hash = repo_path.calculateHash(repo_dir)
            if current_hash != expected_hash:
                return False

        return True

    def lookup(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str],
               repo_dir: Path) -> Optional[Path]:
        """Look up cached output for given source file and tool command.

        This is the optimized fast path that doesn't run /showIncludes.
        It only checks file hashes against cached values.

        Args:
            source_repo_path: RepoPath for source file
            tool_name: Name of the tool
            tool_args: Tool arguments list
            repo_dir: Repository root

        Returns:
            Cache entry directory path if found, None otherwise
        """
        source_key = str(source_repo_path)  # Use repo-relative path as key

        if source_key not in self.index:
            return None

        for entry in self.index[source_key]:
            if entry.get("tool_name") != tool_name:
                continue
            if entry.get("tool_args") != tool_args:
                continue
            if entry.get("main_file_path") != source_key:
                continue

            # For repo mode, verify main_file content hasn't changed
            if entry.get("repo_mode", False) and "main_file_hash" in entry:
                if source_repo_path.calculateHash(repo_dir) != entry["main_file_hash"]:
                    continue

            if not self._dependencies_match(entry["dependencies"], repo_dir):
                continue

            cache_entry_dir = self.cache_dir / entry["cache_key"]
            if cache_entry_dir.exists():
                return cache_entry_dir

        return None

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
            repo_dir: Repository directory (for hashing dependencies)
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
                shutil.copy2(output_file, dest)
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

        if source_key not in self.index:
            self.index[source_key] = []

        index_entry = {
            "cache_key": cache_key,
            "tool_name": tool_name,
            "tool_args": tool_args,
            "main_file_path": source_key,  # repo-relative path
            "dependencies": dep_metadata
        }

        if repo_mode:
            index_entry["repo_mode"] = True
            index_entry["main_file_hash"] = source_repo_path.calculateHash(repo_dir)
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
            repo_dir: Repository directory

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
        env = quicken._get_msvc_environment() if tool_class.needs_vcvars else None

        return tool_class(tool_path, arguments, logger, config, cache,
                         env, optimization)

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

    def _get_msvc_environment(self) -> Dict:
        """Get MSVC environment variables (cached after first call)."""
        if self._msvc_env is not None:
            return self._msvc_env

        vcvarsall = ToolCmd.get_tool_path(self.config, "vcvarsall")
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

    def _get_local_dependencies(self, source_file: Path, repo_dir: Path) -> List[Path]:
        """Get list of local (repo) file dependencies using MSVC /showIncludes."""
        cl_path = ToolCmd.get_tool_path(self.config, "cl")
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

        return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
