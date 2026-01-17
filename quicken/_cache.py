"""
Cache management for Quicken.

Provides FileMetadata and QuickenCache classes for managing
cached tool outputs based on source file and dependency metadata.
"""

import hashlib
import json
import msvcrt
import os
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from ._cpp_normalizer import hash_cpp_source
from ._repo_path import RepoPath
from ._tool_cmd import ToolRunResult


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
        file_path = repo_path.to_absolute_path(repo_dir)

        return hash_cpp_source(file_path)

    def __init__(self, path: RepoPath, file_hash: str, mtime_ns: int, size: int):
        """Initialize file metadata.
        Args:    path: RepoPath instance for the file
                 file_hash: 16-character hex string (64-bit BLAKE2b hash)
                 mtime_ns: Modification time in nanoseconds
                 size: File size in bytes"""
        self.path = path
        self.hash = file_hash
        self.mtime_ns = mtime_ns
        self.size = size

    @classmethod
    def from_dict(cls, data: Dict) -> 'FileMetadata':
        """Load from JSON dictionary.
        Args:    data: Dictionary with 'path', 'hash', 'mtime_ns', 'size' keys
        Returns: FileMetadata instance"""
        repo_path = RepoPath.from_relative_string(data["path"])
        return cls(
            path=repo_path,
            file_hash=data["hash"],
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
        file_path = repo_path.to_absolute_path(repo_dir)
        stat = file_path.stat()
        return cls(
            path=repo_path,
            file_hash=cls.calculate_hash(repo_path, repo_dir),
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

        file_path = self.path.to_absolute_path(repo_dir)
        try:
            stat = os.stat(file_path)
        except (FileNotFoundError, OSError):
            return False, None
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


class CacheMetadata:
    """Metadata for a cached tool execution result stored in metadata.json."""

    def __init__(self, cache_key: str, source_file: str, tool_name: str,
                 tool_args: List[str], main_file_path: str,
                 dependencies: List[FileMetadata], files: List[str],
                 stdout: str, stderr: str, returncode: int, repo_dir: str):
        self.cache_key = cache_key
        self.source_file = source_file
        self.tool_name = tool_name
        self.tool_args = tool_args
        self.main_file_path = main_file_path
        self.dependencies = dependencies
        self.files = files
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.repo_dir = repo_dir

    @classmethod
    def from_file(cls, metadata_file: Path, repo_dir: Path) -> 'CacheMetadata':
        """Load from metadata.json file."""
        with open(metadata_file, 'r') as f:
            data = json.load(f)
        return cls.from_dict(data, repo_dir)

    @classmethod
    def from_dict(cls, data: Dict, repo_dir: Path) -> 'CacheMetadata':
        """Load from JSON dictionary."""
        return cls(
            cache_key=data["cache_key"],
            source_file=data["source_file"],
            tool_name=data["tool_name"],
            tool_args=data["tool_args"],
            main_file_path=data["main_file_path"],
            dependencies=[FileMetadata.from_dict(d) for d in data["dependencies"]],
            files=data["files"],
            stdout=data["stdout"],
            stderr=data["stderr"],
            returncode=data["returncode"],
            repo_dir=data.get("repo_dir", str(repo_dir))
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "cache_key": self.cache_key,
            "source_file": self.source_file,
            "tool_name": self.tool_name,
            "tool_args": self.tool_args,
            "main_file_path": self.main_file_path,
            "dependencies": [d.to_dict() for d in self.dependencies],
            "files": self.files,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "returncode": self.returncode,
            "repo_dir": self.repo_dir
        }

    def save(self, metadata_file: Path):
        """Save to metadata.json file."""
        with open(metadata_file, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)


class CacheEntry:
    """A single entry in the folder index mapping cache_key to dependencies."""

    def __init__(self, cache_key: str, dependencies: List[FileMetadata]):
        self.cache_key = cache_key
        self.dependencies = dependencies

    @classmethod
    def from_dict(cls, data: Dict) -> 'CacheEntry':
        """Load from JSON dictionary."""
        return cls(
            cache_key=data["cache_key"],
            dependencies=[FileMetadata.from_dict(d) for d in data["dependencies"]]
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "cache_key": self.cache_key,
            "dependencies": [d.to_dict() for d in self.dependencies]
        }


class FolderIndex:
    """Index tracking cache entries within a compound cache folder."""

    def __init__(self, compound_key: str, next_entry_id: int, entries: List[CacheEntry]):
        self.compound_key = compound_key
        self.next_entry_id = next_entry_id
        self.entries = entries

    @classmethod
    def from_file(cls, folder_path: Path) -> 'FolderIndex':
        """Load from folder_index.json file."""
        index_file = folder_path / "folder_index.json"
        try:
            with open(index_file, 'r') as f:
                data = json.load(f)
            return cls.from_dict(data)
        except (FileNotFoundError, json.JSONDecodeError):
            return cls(compound_key="", next_entry_id=1, entries=[])

    @classmethod
    def from_dict(cls, data: Dict) -> 'FolderIndex':
        """Load from JSON dictionary."""
        return cls(
            compound_key=data["compound_key"],
            next_entry_id=data["next_entry_id"],
            entries=[CacheEntry.from_dict(e) for e in data["entries"]]
        )

    def to_dict(self) -> Dict:
        """Convert to dictionary for JSON serialization."""
        return {
            "compound_key": self.compound_key,
            "next_entry_id": self.next_entry_id,
            "entries": [e.to_dict() for e in self.entries]
        }

    def save(self, folder_path: Path):
        """Save to folder_index.json file."""
        folder_path.mkdir(parents=True, exist_ok=True)
        index_file = folder_path / "folder_index.json"
        with open(index_file, 'w') as f:
            json.dump(self.to_dict(), f, indent=2)

    def allocate_entry_id(self) -> str:
        """Allocate and return a new cache entry key."""
        cache_key = f"entry_{self.next_entry_id:06d}"
        self.next_entry_id += 1
        return cache_key

    def find_entry_by_cache_key(self, cache_key: str) -> Optional[CacheEntry]:
        """Find entry by cache_key."""
        for entry in self.entries:
            if entry.cache_key == cache_key:
                return entry
        return None

    def add_entry(self, cache_key: str, dependencies: List[FileMetadata]):
        """Add a new cache entry."""
        self.entries.append(CacheEntry(cache_key, dependencies))


def make_args_repo_relative(args: List[str], repo_dir: Path) -> List[str]:
    """Convert file/folder paths in args to repo-relative paths.
    Converts absolute paths inside the repo to repo-relative paths.
    Keeps paths outside the repo as absolute paths.
    Preserves flag arguments (starting with - or /) and non-path arguments as-is.
    Args:    args: Arguments that may contain file paths
             repo_dir: Repository root directory
    Returns: List of arguments with repo paths made relative"""
    result = []
    for arg in args:
        # Skip obvious flag arguments
        if arg.startswith('-') or arg.startswith('/'):
            result.append(arg)
            continue

        try:
            repo_path = RepoPath(repo_dir, Path(arg))
            result.append(str(repo_path))
        except (ValueError, OSError):
            # Path outside repo or can't parse as path - keep as-is
            result.append(arg)

    return result


class CacheKey:
    """Identifies a cache entry by source file, tool, and arguments.

    Computes the cache key string and folder name from the parameters.
    Takes a ToolCmd and computes modified args and repo-relative input args internally.
    """

    def __init__(self, source_repo_path: RepoPath, tool_cmd, repo_dir: Path):
        self.source_repo_path = source_repo_path
        self.tool_name = tool_cmd.tool_name
        self.tool_args = tool_cmd.add_optimization_flags(tool_cmd.arguments)
        self.input_args = make_args_repo_relative(tool_cmd.input_args, repo_dir)

        # Compute derived values eagerly (used in every lookup/store)
        self.key = self._get_key()
        self.folder_name = self._get_folder_name()

    def _get_key(self) -> str:
        """Build cache key string: 'file::tool::args::input_args'"""
        source_key = str(self.source_repo_path)
        args_str = json.dumps(self.tool_args, separators=(',', ':'))
        input_args_str = json.dumps(self.input_args, separators=(',', ':'))
        return f"{source_key}::{self.tool_name}::{args_str}::{input_args_str}"

    def _get_folder_name(self) -> str:
        """Build folder name: 'filename_toolname_hash'"""
        # Extract just filename from path (e.g., "main.cpp" from "src/main.cpp")
        filename = Path(str(self.source_repo_path)).name

        # Sanitize filename for filesystem (replace problematic chars)
        sanitized_filename = filename.replace('\\', '_').replace('/', '_').replace(':', '_')

        # Hash: full_repo_path + tool_name + args + input_args
        args_str = json.dumps(self.tool_args, separators=(',', ':'))
        input_args_str = json.dumps(self.input_args, separators=(',', ':'))
        hash_input = f"{str(self.source_repo_path)}::{self.tool_name}::{args_str}::{input_args_str}"
        compound_hash = hashlib.blake2b(hash_input.encode('utf-8'), digest_size=8).hexdigest()

        return f"{sanitized_filename}_{self.tool_name}_{compound_hash}"


class QuickenCache:
    """Manages caching of tool outputs based on source file and dependency metadata."""

    def __init__(self, cache_dir: Path):
        self.cache_dir = cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        # Thread pool for async file restoration (max 8 concurrent copy operations)
        self._copy_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="quicken_copy")

    def _try_acquire_folder_lock(self, folder_path: Path):
        """Try non-blocking exclusive lock. Returns file handle or None."""
        folder_path.mkdir(parents=True, exist_ok=True)
        lock_path = folder_path / ".lock"
        try:
            f = open(lock_path, 'w')
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            return f
        except (IOError, OSError):
            try:
                f.close()
            except (IOError, OSError):
                pass
            return None

    def _release_folder_lock(self, lock_handle):
        """Release lock."""
        if lock_handle:
            try:
                msvcrt.locking(lock_handle.fileno(), msvcrt.LK_UNLCK, 1)
                lock_handle.close()
            except (IOError, OSError):
                pass

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

    def _check_entry_mtime_match(self, cached_deps: List[FileMetadata], repo_dir: Path) -> bool:
        """Check if all dependencies match by mtime+size (no hashing).
        Args:    cached_deps: List of FileMetadata from cache entry
                 repo_dir: Repository root directory
        Returns: True if all dependencies match by mtime+size, False otherwise"""
        for cached_dep in cached_deps:
            if not cached_dep.path:
                return False

            file_path = cached_dep.path.to_absolute_path(repo_dir)
            try:
                stat = file_path.stat()
            except (FileNotFoundError, OSError):
                return False

            if stat.st_mtime_ns != cached_dep.mtime_ns or stat.st_size != cached_dep.size:
                return False

        return True

    def _check_entry_hash_match(self, cached_deps: List[FileMetadata], repo_dir: Path, hash_cache: Dict = None) -> Optional[List[FileMetadata]]:
        """Check if all dependencies match by hash (hash only files with changed mtime/size).
        Early exit on first hash mismatch. Allows size differences.
        Uses hash_cache to avoid recomputing hashes for files already hashed.
        Args:    cached_deps: List of FileMetadata from cache entry
                 repo_dir: Repository root directory
                 hash_cache: Optional dict mapping RepoPath to hash (updated in-place)
        Returns: List of FileMetadata with updated mtimes/sizes if all match, None otherwise"""
        if hash_cache is None:
            hash_cache = {}

        updated_deps = []

        for cached_dep in cached_deps:
            if not cached_dep.path:
                return None

            file_path = cached_dep.path.to_absolute_path(repo_dir)
            if not file_path.is_file():
                return None

            stat = file_path.stat()
            current_mtime_ns = stat.st_mtime_ns
            current_size = stat.st_size

            # Fast path: mtime+size match -> reuse cached hash (no calculation)
            if current_mtime_ns == cached_dep.mtime_ns and current_size == cached_dep.size:
                updated_deps.append(cached_dep)
                continue

            # Check if we've already hashed this file
            cache_key = (str(cached_dep.path), current_mtime_ns, current_size)
            current_hash = hash_cache.get(cache_key)

            if current_hash is None:
                # Mtime or size changed -> hash this file and cache result
                current_hash = FileMetadata.calculate_hash(cached_dep.path, repo_dir)
                hash_cache[cache_key] = current_hash

            if current_hash != cached_dep.hash:
                return None  # Early exit on first mismatch

            # Hash matches -> create updated metadata with new mtime and size
            updated_deps.append(FileMetadata(
                cached_dep.path,
                cached_dep.hash,  # Same hash
                current_mtime_ns,  # Updated mtime
                current_size  # Updated size (may differ from cached)
            ))

        return updated_deps

    def _get_cache_folder_info(self, cache_key: CacheKey) -> Tuple[Optional[Path], Optional[FolderIndex]]:
        """Get cache folder path and index for the given cache key.
        Performs folder index loading (file I/O + JSON parsing).
        Returns: Tuple of (folder_path, folder_index) or (None, None) if folder doesn't exist"""
        folder_path = self.cache_dir / cache_key.folder_name

        if not folder_path.exists():
            return None, None

        folder_index = FolderIndex.from_file(folder_path)
        return folder_path, folder_index

    def lookup(self, cache_key: CacheKey, repo_dir: Path) -> Optional[Path]:
        """Look up cached output using two-pass strategy: mtime first, then hash.

        Pass 1: Check all entries for mtime+size match (no hashing - fast)
        Pass 2: Check all entries using hash comparison (only if pass 1 fails)

        Returns: Cache entry directory path if found, None otherwise"""

        # Load folder info once
        folder_path, folder_index = self._get_cache_folder_info(cache_key)

        if folder_path is None:
            return None

        # Pass 1: Try mtime+size match (fast path - no hashing)
        for entry in folder_index.entries:
            if self._check_entry_mtime_match(entry.dependencies, repo_dir):
                cache_entry_dir = folder_path / entry.cache_key
                if cache_entry_dir.exists():
                    return cache_entry_dir

        # Pass 2: Try hash-based matching (hash only changed files)
        hash_cache = {}

        for entry in folder_index.entries:
            updated_deps = self._check_entry_hash_match(entry.dependencies, repo_dir, hash_cache)
            if updated_deps is None:
                continue

            cache_entry_dir = folder_path / entry.cache_key
            if not cache_entry_dir.exists():
                continue

            # Try to update mtime - skip if can't acquire lock
            lock_handle = self._try_acquire_folder_lock(folder_path)
            if lock_handle is not None:
                try:
                    entry.dependencies = updated_deps

                    metadata_file = cache_entry_dir / "metadata.json"
                    metadata = CacheMetadata.from_file(metadata_file, repo_dir)
                    metadata.dependencies = updated_deps
                    metadata.save(metadata_file)

                    folder_index.save(folder_path)
                finally:
                    self._release_folder_lock(lock_handle)

            return cache_entry_dir

        return None

    def store(self, cache_key: CacheKey, dependency_repo_paths: List[RepoPath],
              result: ToolRunResult, repo_dir: Path) -> Optional[Path]:
        """Store tool output in cache with dependency hashes.
        Args:    cache_key: CacheKey identifying the cache entry
                 dependency_repo_paths: List of RepoPath instances for dependencies
                 result: ToolRunResult containing output files, stdout, stderr, returncode
                 repo_dir: Repository root directory
        Returns: Path to cache entry directory, or None if lock couldn't be acquired"""

        folder_path = self.cache_dir / cache_key.folder_name

        lock_handle = self._try_acquire_folder_lock(folder_path)
        if lock_handle is None:
            return None

        try:
            return self._store_locked(cache_key, dependency_repo_paths, result, folder_path, repo_dir)
        finally:
            self._release_folder_lock(lock_handle)

    def _store_locked(self, cache_key: CacheKey, dependency_repo_paths: List[RepoPath],
                      result: ToolRunResult, folder_path: Path, repo_dir: Path) -> Path:
        """Internal store implementation, called while holding folder lock."""
        source_key = str(cache_key.source_repo_path)  # repo-relative path

        # Create FileMetadata objects from RepoPath instances
        dep_metadata = [FileMetadata.from_file(dep, repo_dir) for dep in dependency_repo_paths]

        folder_index = FolderIndex.from_file(folder_path)

        # Check if an entry with these exact dependencies already exists in this folder
        dep_hash_str = self._hash_dependencies(dep_metadata)
        existing_entry = None
        for entry in folder_index.entries:
            entry_dep_hash = self._hash_dependencies(entry.dependencies)
            if entry_dep_hash == dep_hash_str:
                existing_entry = entry
                break

        if existing_entry:
            # Reuse existing cache entry - just update metadata with current mtime/size
            entry_key = existing_entry.cache_key
            cache_entry_dir = folder_path / entry_key

            # Update metadata with current mtime and size values
            metadata_file = cache_entry_dir / "metadata.json"
            metadata = CacheMetadata.from_file(metadata_file, repo_dir)
            metadata.dependencies = dep_metadata
            metadata.save(metadata_file)

            # Update the folder index entry
            existing_entry.dependencies = dep_metadata
        else:
            # Create new cache entry
            entry_key = folder_index.allocate_entry_id()

            cache_entry_dir = folder_path / entry_key
            cache_entry_dir.mkdir(parents=True, exist_ok=True)

            stored_files = []
            for output_file in result.output_files:
                if output_file.exists():
                    try:
                        rel_path = output_file.relative_to(repo_dir)
                        dest = cache_entry_dir / rel_path
                        file_path_str = str(rel_path)
                    except ValueError:
                        dest = cache_entry_dir / output_file.name
                        file_path_str = output_file.name

                    dest.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(output_file, dest)
                    stored_files.append(file_path_str)

            metadata = CacheMetadata(
                cache_key=entry_key,
                source_file=source_key,
                tool_name=cache_key.tool_name,
                tool_args=cache_key.tool_args,
                main_file_path=source_key,
                dependencies=dep_metadata,
                files=stored_files,
                stdout=result.stdout,
                stderr=result.stderr,
                returncode=result.returncode,
                repo_dir=str(repo_dir)
            )
            metadata.save(cache_entry_dir / "metadata.json")

            # Add new entry to folder index
            folder_index.add_entry(entry_key, dep_metadata)

        # Set compound_key in folder_index (always, to ensure it's current)
        folder_index.compound_key = cache_key.key

        # Save folder index
        folder_index.save(folder_path)

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
                        main_file_path: str, dependencies: List[FileMetadata], files: List[str]) -> str:
        """Translate absolute paths in text from old repo location to new repo location.
        Only translates paths for explicitly tracked files (main file, dependencies, artifacts).
        Paths are normalized (no ..) before replacement.
        Args:    text: Text to translate (stdout or stderr)
                 old_repo_dir: Old repository root (normalized)
                 new_repo_dir: New repository root (normalized)
                 main_file_path: Repo-relative path to main source file
                 dependencies: List of FileMetadata instances
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
            dep_rel_path = str(dep.path)
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

    def restore(self, cache_entry_dir: Path, repo_dir: Path) -> Tuple[str, str, int]:
        """Restore cached files to repository with parallel copy.
        Each file is copied on a separate thread for maximum parallelism (up to 8 concurrent).
        Handles both flat files and directory trees using relative paths.
        Translates absolute paths in stdout/stderr from cached repo location to current location.
        Returns: Tuple of (stdout, stderr, returncode)"""
        metadata = CacheMetadata.from_file(cache_entry_dir / "metadata.json", repo_dir)

        # Collect all unique parent directories
        folders = set()
        for file_path_str in metadata.files:
            dest = repo_dir / file_path_str
            folders.add(dest.parent)

        # Create all directories upfront in main thread to avoid repeated makedirs calls
        for folder in folders:
            os.makedirs(folder, exist_ok=True)

        # Submit one copy job per file to thread pool for parallel execution
        futures = [
            self._copy_executor.submit(self._copy_file, cache_entry_dir, repo_dir, file_path_str)
            for file_path_str in metadata.files
        ]

        # Translate paths in stdout/stderr from old repo location to new location
        new_repo_dir = str(repo_dir)
        stdout = metadata.stdout
        stderr = metadata.stderr
        if metadata.repo_dir != new_repo_dir:
            stdout = self._translate_paths(stdout, metadata.repo_dir, new_repo_dir,
                                           metadata.main_file_path, metadata.dependencies, metadata.files)
            stderr = self._translate_paths(stderr, metadata.repo_dir, new_repo_dir,
                                           metadata.main_file_path, metadata.dependencies, metadata.files)

        # Wait for all copy operations to complete
        for future in futures:
            future.result(timeout=60)

        return stdout, stderr, metadata.returncode

    def clear(self):
        """Clear all cached entries."""
        if self.cache_dir.exists():
            for entry in self.cache_dir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry)
                else:
                    entry.unlink()

