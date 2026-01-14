"""
Repository path handling for Quicken.

Provides RepoPath class for managing file paths relative to a repository root.
"""

import os
from pathlib import Path


class RepoPath:
    """Stores a path to a file in the repo, relative to the repo. The file does not have to exist.

    If the path is outside the repo, self.path is set to None and the object evaluates to False.
    """

    @classmethod
    def from_relative_string(cls, path_str: str) -> 'RepoPath':
        """Create from known-valid repo-relative path (e.g., from cache).
        Skips validation since cached paths are already normalized and relative."""
        obj = object.__new__(cls)
        obj.path = Path(path_str) if path_str else None
        return obj

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
