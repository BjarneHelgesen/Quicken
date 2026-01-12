"""
Cache management for Quicken.

Provides FileMetadata and QuickenCache classes for managing
cached tool outputs based on source file and dependency metadata.
"""

import hashlib
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from concurrent.futures import ThreadPoolExecutor

from ._cpp_normalizer import hash_cpp_source
from ._repo_path import RepoPath


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
                 repo_dir: Repository directory (for hashing dependencies and output paths)
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

        stdout = self._translate_paths(metadata["stdout"], old_repo_dir, new_repo_dir, main_file_path, dependencies, files)
        stderr = self._translate_paths(metadata["stderr"], old_repo_dir, new_repo_dir, main_file_path, dependencies, files)

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
