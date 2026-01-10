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

from .cpp_normalizer import hash_cpp_source


class RepoPath:
    """Stores a path to a file in the repo, relative to the repo. The file does not have to exist.

    If the path is outside the repo, self.path is set to None and the object evaluates to False.
    """
    def __init__(self, repo: Path, path: Path):
        """Initialize RepoPath.
        Args:    repo: Repository root (must be an absolute path)
                 path: Path to convert (absolute or relative to repo)
        If path is outside repo, sets self.path = None."""
        try:
            if not path.is_absolute():
                path = repo / path

            path = Path(os.path.normpath(path)) # Normalize to remove .. and .
            self.path = path.relative_to(repo)
        except (ValueError, OSError):
            # Path is outside repo or invalid
            self.path = None


    def toAbsolutePath(self, repo: Path) -> Path:
        """Convert this repo-relative path to an absolute path.
        Args:    repo: Repository root directory
        Returns: Absolute path by joining repo with relative path, or None if invalid"""
        if self.path is None:
            return None
        return repo / self.path

    def __str__(self) -> str:
        """Return POSIX-style string representation for serialization.
        Uses forward slashes for cross-platform compatibility in JSON.
        Returns empty string if path is None."""
        if self.path is None:
            return ""
        return self.path.as_posix()

    def __bool__(self):
        return self.path is not None


class FileMetadata:
    """Metadata for a single file in the cache.

    Stores file path (repo-relative), content hash, modification time, and size.
    Used for dependency tracking and cache validation.
    """

    @staticmethod
    def calculate_hash(repo_path: RepoPath, repo_dir: Path) -> str:
        """Calculate 64-bit hash of the file at the given repo path.
        Uses whitespace and comment-insensitive hashing to maximize 
        cache hits on formatting changes.
        Args:    repo_path: RepoPath instance for the file
                 repo_dir: Repository root directory
        Returns: 16-character hex string (64-bit BLAKE2b hash), or None if invalid path"""
        if not repo_path:
            return None
        file_path = repo_path.toAbsolutePath(repo_dir)

        return hash_cpp_source(file_path)

    def __init__(self, path: RepoPath, hash: str, mtime_ns: int, size: int):
        """Initialize file metadata.
        Args:    path: RepoPath instance for the file
                 hash: 16-character hex string (64-bit BLAKE2b hash)
                 mtime_ns: Modification time in nanoseconds
                 size: File size in bytes"""
        self.path = path
        self.hash = hash
        self.mtime_ns = mtime_ns
        self.size = size

    @classmethod
    def from_dict(cls, data: Dict, repo_dir: Path) -> 'FileMetadata':
        """Load from JSON dictionary.
        Args:    data: Dictionary with 'path', 'hash', 'mtime_ns', 'size' keys
                 repo_dir: Repository root directory to create RepoPath
        Returns: FileMetadata instance"""
        repo_path = RepoPath(repo_dir, Path(data["path"]))
        return cls(
            path=repo_path,
            hash=data["hash"],
            mtime_ns=data["mtime_ns"],
            size=data["size"]
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization.
        Returns: Dictionary with 'path', 'hash', 'mtime_ns', 'size' keys"""
        return {
            "path": str(self.path),
            "hash": self.hash,
            "mtime_ns": self.mtime_ns,
            "size": self.size
        }

    @classmethod
    def from_file(cls, repo_path: RepoPath, repo_dir: Path) -> 'FileMetadata':
        """Create by reading file from disk.
        Args:    repo_path: RepoPath instance for the file
                 repo_dir: Repository root directory
        Returns: FileMetadata instance with current file state"""
        file_path = repo_path.toAbsolutePath(repo_dir)
        stat = file_path.stat()
        return cls(
            path=repo_path,
            hash=cls.calculate_hash(repo_path, repo_dir),
            mtime_ns=stat.st_mtime_ns,
            size=stat.st_size
        )

    def matches_current_file(self, repo_dir: Path) -> Tuple[bool, Optional['FileMetadata']]:
        """Check if metadata matches current file state.

        Fast path: Check mtime_ns+size first, only hash if mtime changed.

        Args:    repo_dir: Repository root directory
        Returns: Tuple of (matches, updated_metadata) where:
                 - matches: True if file content matches cached hash
                 - updated_metadata: FileMetadata with current mtime if hash matches,
                                    None if no match or file doesn't exist"""
        if not self.path:
            return False, None

        file_path = self.path.toAbsolutePath(repo_dir)
        if not file_path.is_file():
            return False, None

        stat = file_path.stat()
        current_mtime_ns = stat.st_mtime_ns
        current_size = stat.st_size

        # Fast path: unchanged
        if current_mtime_ns == self.mtime_ns and current_size == self.size:
            return True, self

        # Size changed = different file
        if current_size != self.size:
            return False, None

        # Mtime changed - verify hash
        current_hash = FileMetadata.calculate_hash(self.path, repo_dir)
        if current_hash != self.hash:
            return False, None

        # Hash matches - return updated metadata
        return True, FileMetadata(self.path, self.hash, current_mtime_ns, current_size)

    def __repr__(self):
        return f"FileMetadata({self.path!r}, hash={self.hash[:8]}..., size={self.size})"


class QuickenCache:
    """Manages caching of tool outputs based on source file and dependency metadata."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.index_file = cache_dir / "index.json"
        self.index = self._load_index()
        self._next_id = self._get_next_id()
        self.dep_hash_index = self._build_dep_hash_index()
        # Thread pool for async file restoration (max 8 concurrent copy operations)
        self._copy_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="quicken_copy")

    def _load_index(self) -> Dict:
        """Load the cache index.
        Index structure (flat dictionary with compound keys, supporting collisions):
        {
            "src/main.cpp::1234::cl::['/c','/W4']": [
                {
                    "cache_key": "entry_001",
                    "dependencies": [
                        {"path": "src/main.cpp", "hash": "a1b2c3d4e5f60708", "mtime_ns": ..., "size": ...},
                        {"path": "include/header.h", "hash": "b2c3d4e5f6071809", "mtime_ns": ..., "size": ...},
                        ...
                    ]
                },
                {
                    "cache_key": "entry_005",
                    "dependencies": [...]  # Different content, same size
                }
            ],
            ...
        }"""
        try:
            with open(self.index_file, 'r') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            # Index file doesn't exist or is corrupted, start fresh
            return {}

    def _save_index(self):
        """Save the cache index."""
        with open(self.index_file, 'w') as f:
            json.dump(self.index, f, indent=2)

    def _get_next_id(self) -> int:
        """Get next available cache entry ID."""
        max_id = 0
        for entries in self.index.values():
            # entries is now a list of cache entries
            for entry in entries:
                cache_key = entry.get("cache_key", "")
                if cache_key.startswith("entry_"):
                    entry_id = int(cache_key.split("_")[1])
                    max_id = max(max_id, entry_id)
        return max_id + 1

    def _build_dep_hash_index(self) -> Dict[Tuple[str, str], str]:
        """Build index mapping (compound_key, dependency_hash) to cache keys.
        This allows finding existing cache entries with the same dependencies
        for the same compound key (file+size+tool+args) to avoid creating duplicates.
        Returns: Dict mapping (compound_key, dep_hash) to cache_key"""
        dep_hash_index = {}
        for compound_key, entries in self.index.items():
            # entries is now a list of cache entries
            for entry in entries:
                cache_key = entry.get("cache_key", "")
                dependencies_dicts = entry.get("dependencies", [])
                if dependencies_dicts:
                    # Hash dependencies directly from dicts
                    dep_hash_str = self._hash_dependencies_from_dicts(dependencies_dicts)
                    dep_hash_index[(compound_key, dep_hash_str)] = cache_key
        return dep_hash_index

    def _hash_dependencies_from_dicts(self, dependencies: List[Dict]) -> str:
        """Calculate hash of all dependency hashes combined from dict representation.
        Args:    dependencies: List of dependency dicts with 'path' and 'hash' keys
        Returns: 16-character hex string (64-bit hash of all dependency hashes)"""
        hash_obj = hashlib.blake2b(digest_size=8)
        for dep in dependencies:
            # Hash combination of path and content hash for uniqueness
            dep_str = f"{dep['path']}:{dep['hash']}"
            hash_obj.update(dep_str.encode('utf-8'))
        return hash_obj.hexdigest()

    def _hash_dependencies(self, dependencies: List[FileMetadata]) -> str:
        """Calculate hash of all dependency hashes combined.
        Args:    dependencies: List of FileMetadata instances
        Returns: 16-character hex string (64-bit hash of all dependency hashes)"""
        hash_obj = hashlib.blake2b(digest_size=8)
        for dep in dependencies:
            # Hash combination of path and content hash for uniqueness
            dep_str = f"{str(dep.path)}:{dep.hash}"
            hash_obj.update(dep_str.encode('utf-8'))
        return hash_obj.hexdigest()

    def _translate_input_args_for_cache_key(self, input_args: List[str], repo_dir: Path) -> List[str]:
        """Translate file/folder paths in input_args to repo-relative for cache key portability.
        Converts absolute paths to files/folders in the repo to repo-relative paths.
        Keeps paths outside the repo as absolute paths.
        Preserves flag arguments (starting with - or /) and non-path arguments as-is.
        Args:    input_args: Input arguments containing file paths
                 repo_dir: Repository root directory
        Returns: List of arguments with repo paths made relative and external paths absolute"""

        translated = []
        for arg in input_args:
            # Skip obvious flag arguments (just for code clarity. They would have been filtered out as not paths anyway)
            if arg.startswith('-') or arg.startswith('/'):
                translated.append(arg)
                continue

            try:                
                repo_path = RepoPath(repo_dir, Path(arg))
                translated.append(str(repo_path) if repo_path else arg)

            except (ValueError, OSError):
                # Can't parse as path, keep as-is
                translated.append(arg)

        return translated

    def _make_cache_key(self, source_repo_path: RepoPath, file_size: int, tool_name: str, tool_args: List[str], input_args: List[str]=[], repo_dir: Path = None) -> str:
        """Build compound cache key from source file, size, tool, and args.
        Args:    source_repo_path: RepoPath for source file
                 file_size: Size of source file in bytes
                 tool_name: Name of the tool
                 tool_args: Tool arguments list
                 input_args: Optional input arguments (paths will be translated)
                 repo_dir: Repository root for translating input_args paths
        Returns: Compound key string in format: "file::size::tool::args" or "file::size::tool::args::input_args" """
        source_key = str(source_repo_path)
        args_str = json.dumps(tool_args, separators=(',', ':'))

        # Translate paths in input_args for cache portability
        translated_input_args = self._translate_input_args_for_cache_key(input_args, repo_dir)
        input_args_str = json.dumps(translated_input_args, separators=(',', ':'))
        return f"{source_key}::{file_size}::{tool_name}::{args_str}::{input_args_str}"

    def _check_entry_mtime_match(self, cached_deps: List[FileMetadata], repo_dir: Path) -> bool:
        """Check if all dependencies match by mtime+size (no hashing).
        Args:    cached_deps: List of FileMetadata from cache entry
                 repo_dir: Repository root directory
        Returns: True if all dependencies match by mtime+size, False otherwise"""
        for cached_dep in cached_deps:
            if not cached_dep.path:
                return False

            file_path = cached_dep.path.toAbsolutePath(repo_dir)
            if not file_path.is_file():
                return False

            stat = file_path.stat()
            if stat.st_mtime_ns != cached_dep.mtime_ns or stat.st_size != cached_dep.size:
                return False

        return True

    def _check_entry_hash_match(self, cached_deps: List[FileMetadata], repo_dir: Path) -> Optional[List[FileMetadata]]:
        """Check if all dependencies match by hash (hash only files with changed mtime).
        Args:    cached_deps: List of FileMetadata from cache entry
                 repo_dir: Repository root directory
        Returns: List of FileMetadata with updated mtimes if all match, None otherwise"""
        updated_deps = []

        for cached_dep in cached_deps:
            if not cached_dep.path:
                return None

            file_path = cached_dep.path.toAbsolutePath(repo_dir)
            if not file_path.is_file():
                return None

            stat = file_path.stat()
            current_mtime_ns = stat.st_mtime_ns
            current_size = stat.st_size

            # Fast path: mtime+size match -> reuse cached hash (no calculation)
            if current_mtime_ns == cached_dep.mtime_ns and current_size == cached_dep.size:
                updated_deps.append(cached_dep)
                continue

            # Size changed -> definitely different content
            if current_size != cached_dep.size:
                return None

            # Mtime changed but size same -> hash this file only
            current_hash = FileMetadata.calculate_hash(cached_dep.path, repo_dir)
            if current_hash != cached_dep.hash:
                return None

            # Hash matches -> create updated metadata with new mtime
            updated_deps.append(FileMetadata(
                cached_dep.path,
                cached_dep.hash,  # Same hash
                current_mtime_ns,  # Updated mtime
                current_size
            ))

        return updated_deps

    def lookup(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str],
               repo_dir: Path, input_args: List[str] = []) -> Optional[Path]:
        """Look up cached output for given source file and tool command.
        Two-phase approach:
        Phase 1: Try all entries for mtime-only match (no hashing).
        Phase 2: Hash only files whose mtime changed.
        Args:    source_repo_path: RepoPath for source file
                 tool_name: Name of the tool
                 tool_args: Tool arguments list
                 repo_dir: Repository root
                 input_args: Optional input arguments with file paths
        Returns: Cache entry directory path if found, None otherwise"""
        # Get file size (fast - from stat)
        file_path = source_repo_path.toAbsolutePath(repo_dir)
        file_size = file_path.stat().st_size

        # Build compound key for O(1) lookup
        compound_key = self._make_cache_key(source_repo_path, file_size, tool_name, tool_args, input_args, repo_dir)

        if compound_key not in self.index:
            return None

        # Get list of entries with this key (may have collisions)
        entries = self.index[compound_key]

        # PHASE 1: Try to find entry where ALL dependencies match by mtime+size (no hashing)
        for entry in entries:
            cached_deps = [FileMetadata.from_dict(d, repo_dir) for d in entry["dependencies"]]

            if self._check_entry_mtime_match(cached_deps, repo_dir):
                cache_entry_dir = self.cache_dir / entry["cache_key"]
                if cache_entry_dir.exists():
                    return cache_entry_dir

        # PHASE 2: No mtime match - try hash-based matching (hash only changed files)
        for entry in entries:
            cached_deps = [FileMetadata.from_dict(d, repo_dir) for d in entry["dependencies"]]

            updated_deps = self._check_entry_hash_match(cached_deps, repo_dir)
            if updated_deps is None:
                continue  # Try next entry

            cache_entry_dir = self.cache_dir / entry["cache_key"]
            if not cache_entry_dir.exists():
                continue  # Try next entry

            # We're in phase 2, so at least one mtime must have changed
            # Update both index and metadata.json with new mtimes
            entry["dependencies"] = [d.to_dict() for d in updated_deps]

            metadata_file = cache_entry_dir / "metadata.json"
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            metadata["dependencies"] = [d.to_dict() for d in updated_deps]
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)

            self._save_index()

            return cache_entry_dir

        # No matching entry found
        return None

    def store(self, source_repo_path: RepoPath, tool_name: str, tool_args: List[str],
              dependency_repo_paths: List[RepoPath], output_files: List[Path],
              stdout: str, stderr: str, returncode: int,
              repo_dir: Path,
              output_base_dir: Path = None,
              input_args: List[str] = []) -> Path:
        """Store tool output in cache with dependency hashes.
        Args:    source_repo_path: RepoPath for source file (or main file)
                 tool_name: Name of the tool
                 tool_args: Tool arguments (without main_file path)
                 dependency_repo_paths: List of RepoPath instances for dependencies
                 output_files: List of output file paths
                 stdout: Tool stdout
                 stderr: Tool stderr
                 returncode: Tool exit code
                 repo_dir: Repository directory (for hashing dependencies)
                 output_base_dir: Base directory for preserving relative paths
                 input_args: Optional input arguments with file paths
        Returns: Path to cache entry directory"""
        source_key = str(source_repo_path)  # repo-relative path

        # Create FileMetadata objects from RepoPath instances
        dep_metadata = [FileMetadata.from_file(dep, repo_dir) for dep in dependency_repo_paths]

        # Get file size (fast - from stat)
        file_path = source_repo_path.toAbsolutePath(repo_dir)
        file_size = file_path.stat().st_size

        # Build compound key to check for existing entry with same dependencies
        compound_key = self._make_cache_key(source_repo_path, file_size, tool_name, tool_args, input_args, repo_dir)

        # Check if an entry with these exact dependencies already exists for this compound key
        dep_hash_str = self._hash_dependencies(dep_metadata)
        existing_cache_key = self.dep_hash_index.get((compound_key, dep_hash_str))

        if existing_cache_key:
            # Reuse existing cache entry - just update the index to point to it
            cache_key = existing_cache_key
            cache_entry_dir = self.cache_dir / cache_key

            # Update metadata with current mtime values
            metadata_file = cache_entry_dir / "metadata.json"
            with open(metadata_file, 'r') as f:
                metadata = json.load(f)
            metadata["dependencies"] = [d.to_dict() for d in dep_metadata]
            with open(metadata_file, 'w') as f:
                json.dump(metadata, f, indent=2)
        else:
            # Create new cache entry
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

            metadata = {
                "cache_key": cache_key,
                "source_file": source_key,
                "tool_name": tool_name,
                "tool_args": tool_args,
                "main_file_path": source_key,  # repo-relative path
                "dependencies": [d.to_dict() for d in dep_metadata],
                "files": stored_files,
                "stdout": stdout,
                "stderr": stderr,
                "returncode": returncode,
                "repo_dir": str(repo_dir)  # Normalized absolute path for path translation
            }

            with open(cache_entry_dir / "metadata.json", 'w') as f:
                json.dump(metadata, f, indent=2)

            # Add to dep_hash_index
            self.dep_hash_index[(compound_key, dep_hash_str)] = cache_key

        # Create minimized index entry (no redundant fields)
        index_entry = {
            "cache_key": cache_key,
            "dependencies": [d.to_dict() for d in dep_metadata]
        }

        # Append to list at compound key (supports collisions)
        if compound_key not in self.index:
            self.index[compound_key] = []
        self.index[compound_key].append(index_entry)
        self._save_index()

        return cache_entry_dir

    def _copy_file(self, cache_entry_dir: Path, repo_dir: Path, file_path_str: str):
        """Worker method to copy a single file in background thread.
        Assumes parent directories already exist.
        Args:    cache_entry_dir: Cache entry directory containing source files
                 repo_dir: Repository directory where files will be restored
                 file_path_str: Repo-relative file path"""
        # file_path_str is repo-relative path (e.g., "build/output.o" or "xml/index.xml")
        src = cache_entry_dir / file_path_str
        dest = repo_dir / file_path_str
        shutil.copyfile(src, dest)

    def _translate_paths(self, text: str, old_repo_dir: str, new_repo_dir: str,
                        main_file_path: str, dependencies: List[Dict], files: List[str]) -> str:
        """Translate absolute paths in text from old repo location to new repo location.
        Only translates paths for explicitly tracked files (main file, dependencies, artifacts).
        Paths are normalized (no ..) before replacement.
        Args:    text: Text to translate (stdout or stderr)
                 old_repo_dir: Old repository root (normalized)
                 new_repo_dir: New repository root (normalized)
                 main_file_path: Repo-relative path to main source file
                 dependencies: List of dependency dicts with 'path' keys
                 files: List of repo-relative artifact paths
        Returns: Text with translated paths"""
        if not text or old_repo_dir == new_repo_dir:
            return text

        # Build list of (old_absolute_path, new_absolute_path) tuples
        path_mappings = []

        # Add main file
        old_main = str(Path(old_repo_dir) / main_file_path)
        new_main = str(Path(new_repo_dir) / main_file_path)
        path_mappings.append((old_main, new_main))

        # Add all dependencies
        for dep in dependencies:
            dep_rel_path = dep["path"]
            old_dep = str(Path(old_repo_dir) / dep_rel_path)
            new_dep = str(Path(new_repo_dir) / dep_rel_path)
            path_mappings.append((old_dep, new_dep))

        # Add all artifacts
        for file_rel_path in files:
            old_file = str(Path(old_repo_dir) / file_rel_path)
            new_file = str(Path(new_repo_dir) / file_rel_path)
            path_mappings.append((old_file, new_file))

        # Sort by length descending to replace longer paths first (avoid partial matches)
        path_mappings.sort(key=lambda x: len(x[0]), reverse=True)

        # Replace all old paths with new paths
        result = text
        for old_path, new_path in path_mappings:
            result = result.replace(old_path, new_path)

        return result

    def restore(self, cache_entry_dir: Path, repo_dir: Path) -> int:
        """Restore cached files to repository with parallel copy.
        Each file is copied on a separate thread for maximum parallelism (up to 8 concurrent).
        This method translates paths and prints output while files copy in background.
        Handles both flat files and directory trees using relative paths.
        Translates absolute paths in stdout/stderr from cached repo location to current location.
        Returns: returncode"""
        metadata_file = cache_entry_dir / "metadata.json"
        with open(metadata_file, 'r') as f:
            metadata = json.load(f)

        files = metadata["files"]

        # Collect all unique parent directories
        folders = set()
        for file_path_str in files:
            dest = repo_dir / file_path_str
            folders.add(dest.parent)

        # Create all directories upfront in main thread to avoid repeated makedirs calls
        for folder in folders:
            os.makedirs(folder, exist_ok=True)

        # Submit one copy job per file to thread pool for parallel execution
        futures = [
            self._copy_executor.submit(self._copy_file, cache_entry_dir, repo_dir, file_path_str)
            for file_path_str in files
        ]

        # Translate paths in stdout/stderr from old repo location to new location
        old_repo_dir = metadata.get("repo_dir", str(repo_dir))  # Default to current if not stored
        new_repo_dir = str(repo_dir)
        main_file_path = metadata["main_file_path"]
        dependencies = metadata["dependencies"]

        stdout = self._translate_paths(metadata["stdout"], old_repo_dir, new_repo_dir,
                                       main_file_path, dependencies, files)
        stderr = self._translate_paths(metadata["stderr"], old_repo_dir, new_repo_dir,
                                       main_file_path, dependencies, files)

        print(stdout, end='')
        print(stderr, end='', file=sys.stderr)

        # Wait for all copy operations to complete
        for future in futures:
            future.result(timeout=60)

        return metadata["returncode"]

    def clear(self):
        """Clear all cached entries."""
        # Remove all cache entry directories
        if self.cache_dir.exists():
            for entry in self.cache_dir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry)
                elif entry != self.index_file:
                    entry.unlink()

        # Clear the index and dep_hash_index
        self.index = {}
        self.dep_hash_index = {}
        self._next_id = 1
        self._save_index()


class ToolCmd(ABC):
    """Base class for tool command wrappers."""

    # Class attributes (overridden by subclasses)
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

    def __init__(self, tool_path: str, arguments: List[str], logger, config, cache, msvc_env, optimization=None, output_args=None, input_args=None):
        self.tool_path = tool_path
        self.arguments = arguments
        self.optimization = optimization
        self.config = config
        self.logger = logger
        self.cache = cache
        self.msvc_env = msvc_env  # MSVC environment for /showIncludes
        self.output_args = output_args if output_args is not None else []  # Output-specific arguments (not part of cache key)
        self.input_args = input_args if input_args is not None else []  # Input-specific arguments (part of cache key)

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoPath]:
        """Get list of dependency paths for caching using MSVC /showIncludes.
        Default implementation for C++ tools. Can be overridden by subclasses.
        Args:    main_file: Main file being processed (source file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository root directory
        Returns: List of RepoPath instances for all dependencies"""
        cl_path = ToolCmd.get_tool_path(self.config, "cl")

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        result = subprocess.run(
            [cl_path, '/showIncludes', '/Zs', str(main_file)],
            env=self.msvc_env,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse /showIncludes output
        dependencies = [RepoPath(repo_dir, main_file)]  # Always include the source file itself

        for line in result.stderr.splitlines():  # /showIncludes outputs to stderr
            if line.startswith("Note: including file:"):
                # Extract the file path (after "Note: including file:")
                file_path_str = line.split(":", 2)[2].strip()
                repo_path = RepoPath(repo_dir, file_path_str)
                if repo_path:  # Only include dependencies inside repo
                    dependencies.append(repo_path)

        return dependencies

    @staticmethod
    def get_tool_path(config: Dict, tool_name: str) -> str:
        """Get the full path to a tool from config.
        Args:    config: Configuration dictionary
                 tool_name: Name of the tool
        Returns: Full path to the tool executable"""
        return config[tool_name]

    def get_optimization_flags(self, level: int) -> List[str]:
        """Return optimization flags for the given level.
        Args:    level: Optimization level (0-3)
        Returns: List of flags (may be empty list, or multiple flags for space-separated)"""
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
        Args:    args: Original arguments
        Returns: Modified arguments with optimization flags at beginning"""
        if not self.supports_optimization:
            return args

        # Default to O0 if not specified
        opt_level = self.optimization if self.optimization is not None else 0
        opt_flags = self.get_optimization_flags(opt_level)

        return opt_flags + args

    def build_execution_command(self, main_file: Path = None) -> List[str]:
        """Build complete command for execution.
        Args:    main_file: Main file path for repo-level tools (e.g., Doxyfile) or source file for file-level tools
        Returns: Complete command list for subprocess"""
        modified_args = self.add_optimization_flags(self.arguments)
        cmd = [self.tool_path] + modified_args

        # Add input_args (these are part of the cache key). Note that they are joined as a single argument, as the called decides the spacing.
        if self.input_args:
            cmd.extend(self.input_args)

        # Add main file before output args (some tools expect source file before -o)
        if main_file:
            cmd.append(str(main_file))

        # Append output_args at the end (these are not part of the cache key)
        if self.output_args:
            cmd.extend(self.output_args)

        return cmd

    def try_all_optimization_levels(self, tool_name: str, tool_args: List[str],
                                   source_repo_path: RepoPath, repo_dir: Path) -> Tuple[Optional[Path], List[str]]:
        """Try to find cache hit with any optimization level.
        Args:    tool_name: Name of the tool
                 tool_args: Tool arguments (without source_file/main_file)
                 source_repo_path: RepoPath for source file
                 repo_dir: Repository directory
        Returns: Tuple of (cache_entry, modified_args) or (None, original_args)"""
        if not self.supports_optimization:
            cache_entry = self.cache.lookup(source_repo_path, tool_name, tool_args, repo_dir, self.input_args)
            return cache_entry, tool_args

        for opt_level in range(len(self.optimization_flags)):
            self.optimization = opt_level
            modified_args = self.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, repo_dir, self.input_args)
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

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoPath]:
        """Get dependencies for Doxygen: Doxyfile + all C++ source/header files.
        Args:    main_file: Path to Doxyfile
                 repo_dir: Repository root directory
        Returns: List of RepoPath instances for Doxyfile and all C++ files"""
        dependencies = [RepoPath(repo_dir, main_file)]  # Include Doxyfile itself

        # Add all C++ source and header files in the repo
        for pattern in ['**/*.cpp', '**/*.h', '**/*.hpp']:
            for file_path in repo_dir.glob(pattern):
                repo_path = RepoPath(repo_dir, file_path)
                if repo_path:
                    dependencies.append(repo_path)

        return dependencies

class ToolCmdFactory:
    """Factory for creating ToolCmd instances."""

    _registry = {
        "cl": ClCmd,
        "clang++": ClangCmd,
        "clang-tidy": ClangTidyCmd,
        "doxygen": DoxygenCmd,
    }

    @classmethod
    def create(cls, tool_name: str, tool_path: str, arguments: List[str],
               logger, config, cache, msvc_env, optimization=None, output_args=None, input_args=None) -> ToolCmd:
        """Create ToolCmd instance for the given tool name.
        Args:    tool_name: Name of the tool (must be registered)
                 tool_path: Full path to tool executable
                 arguments: Command-line arguments (part of cache key)
                 logger: Logger instance
                 config: Configuration dict
                 cache: QuickenCache instance
                 msvc_env: MSVC environment dict
                 optimization: Optional optimization level
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key, paths translated to repo-relative)
        Returns: ToolCmd subclass instance
        Raises:  ValueError: If tool_name is not registered"""
        if tool_name not in cls._registry:
            raise ValueError(f"Unsupported tool: {tool_name}")

        tool_class = cls._registry[tool_name]

        return tool_class(tool_path, arguments, logger, config, cache,
                         msvc_env, optimization, output_args, input_args)

class Quicken:
    """Main Quicken application."""

    @staticmethod
    def _get_quicken_data_dir() -> Path:
        """Get Quicken's data directory.
        Returns: Path to ~/.quicken/"""
        return Path.home() / ".quicken"

    def __init__(self, repo_dir: Path, cache_dir: Optional[Path] = None):
        """Initialize Quicken for a specific repository.
        Tools must be configured in ~/.quicken/tools.json (created by installation).
        Args:    repo_dir: Repository root directory (absolute path)
                 cache_dir: Optional cache directory path (defaults to ~/.quicken/cache)"""
        config_path = self._get_quicken_data_dir() / "tools.json"
        self.config = self._load_config(config_path)
        self.repo_dir = repo_dir.absolute()  # Normalize to absolute path
        cache_path = cache_dir if cache_dir else self._get_quicken_data_dir() / "cache"
        self.cache = QuickenCache(cache_path)
        self._setup_logging()
        # Eagerly fetch and cache MSVC environment (assumes MSVC is installed)
        self._msvc_env = self._get_msvc_environment()

    def _load_config(self, config_path: Path) -> Dict:
        """Load tools configuration."""
        with open(config_path, 'r') as f:
            return json.load(f)

    def _setup_logging(self):
        """Set up logging to file."""
        log_dir = self._get_quicken_data_dir()
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
        cache_file = self._get_quicken_data_dir() / "msvc_env.json"

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

    def _get_file_timestamps(self, directory: Path) -> Dict[Path, int]:
        """Get dictionary of file paths to their modification timestamps.
        Arg:     directory: Directory to scan
        Returns: Dictionary mapping file paths to st_mtime_ns timestamps"""
        if not directory.exists():
            return {}
         
        file_timestamps = {}
        for f in directory.rglob("*"): 
            if f.is_file():
                try:
                    file_timestamps[f] = f.stat().st_mtime_ns
                except (OSError, FileNotFoundError):
                    pass

        return file_timestamps

    def _run_tool(self, tool: ToolCmd, tool_args: List[str], source_file: Path,
                  repo_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run the specified tool with arguments.
        Args:    tool: ToolCmd instance
                 tool_args: Arguments to pass to tool (already includes optimization flags)
                 source_file: Path to file to process (C++ file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository directory (scan location for output files)
        Returns: Tuple of (output_files, stdout, stderr, returncode)"""
        files_before = self._get_file_timestamps(repo_dir)

        cmd = tool.build_execution_command(source_file)

        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            env=tool.msvc_env if tool.needs_vcvars else None
        )

        files_after = self._get_file_timestamps(repo_dir)

        # Detect output files: new files OR files with updated timestamps
        output_files = [
            f for f, mtime in files_after.items()
            if f not in files_before or mtime > files_before[f]
        ]

        return output_files, result.stdout, result.stderr, result.returncode

    def run(self, source_file: Path, tool_name: str, tool_args: List[str],
            optimization: int = None, output_args: List[str] = None, input_args: List[str] = []) -> int:
        """Main execution: optimized cache lookup, or get dependencies and run tool.
        Args:    source_file: File to process (absolute or relative path) - C++ file for compilers, Doxyfile for Doxygen
                 tool_name: Tool to run
                 tool_args: Arguments for the tool (part of cache key)
                 optimization: Optimization level (0-3, or None to accept any cached level)
                 output_args: Output-specific arguments (NOT part of cache key, e.g., ['-o', 'output.s'])
                 input_args: Input-specific arguments (part of cache key, paths translated to repo-relative)
        Returns: Tool exit code (integer)"""

        # Convert source_file to RepoPath and validate it's inside repo
        source_repo_path = RepoPath(self.repo_dir, source_file)
        if not source_repo_path:
            raise ValueError(f"Source file {source_file} is outside repository {self.repo_dir}")

        # Convert RepoPath back to absolute path for tool execution
        abs_source_file = source_repo_path.toAbsolutePath(self.repo_dir)

        start_time = time.perf_counter()
        tool_path = ToolCmd.get_tool_path(self.config, tool_name)
        tool = ToolCmdFactory.create(
            tool_name, tool_path, tool_args,
            self.logger, self.config, self.cache, self._msvc_env, optimization, output_args, input_args
        )

        if optimization is None:
            cache_entry, modified_args = tool.try_all_optimization_levels(
                tool_name, tool_args, source_repo_path, self.repo_dir
            )
        else:
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, self.repo_dir, input_args)

        if cache_entry:
            returncode = self.cache.restore(cache_entry, self.repo_dir)
            self.logger.info(f"CACHE HIT - file: {source_repo_path}, tool: {tool_name}, "
                           f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                           f"args: {modified_args}, cache_entry: {cache_entry.name}, "
                           f"returncode: {returncode}")
            return returncode

        # Get dependencies from tool
        dependency_repo_paths = tool.get_dependencies(abs_source_file, self.repo_dir)

        output_files, stdout, stderr, returncode = self._run_tool(tool, modified_args, abs_source_file, self.repo_dir)

        print(stdout, end='')
        print(stderr, end='', file=sys.stderr)

        self.cache.store(
            source_repo_path, tool_name, modified_args, dependency_repo_paths, output_files,
            stdout, stderr, returncode, self.repo_dir,
            output_base_dir=self.repo_dir,
            input_args=input_args
        )
        self.logger.info(f"CACHE MISS - file: {source_repo_path}, tool: {tool_name}, "
                       f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                       f"args: {modified_args}, dependencies: {len(dependency_repo_paths)}, "
                       f"returncode: {returncode}, output_files: {len(output_files)}")

        return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
