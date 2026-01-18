"""
Repository path handling for Quicken.

Provides RepoFile class for managing file paths relative to a repository root.
"""

import os
from pathlib import Path

from ._type_check import typecheck_methods


@typecheck_methods
class RepoFile:
    """Stores a path to a file in the repo, relative to the repo. The file does not have to exist.

    Raises ValueError if the path is outside the repo.
    """

    def __init__(self, repo: Path, path: Path):
        """Initialize RepoFile.
        Args:    repo: Repository root (must be an absolute path)
                 path: Path to convert (absolute or relative to repo)
        Raises:  ValueError if path is outside repo"""
        if not path.is_absolute():
            path = repo / path

        path = Path(os.path.normpath(path))  # Normalize to remove .. and .
        self.path = path.relative_to(repo)  # Raises ValueError if outside repo

    def to_absolute_path(self, repo: Path) -> Path:
        """Convert this repo-relative path to an absolute path.
        Args:    repo: Repository root directory
        Returns: Absolute path by joining repo with relative path"""
        return repo / self.path

    def __str__(self) -> str:
        """Return POSIX-style string representation for serialization.
        Uses forward slashes for cross-platform compatibility in JSON."""
        return self.path.as_posix()


class CachedRepoFile(RepoFile):
    """RepoFile created from a known-valid repo-relative path string (e.g., from cache).
    Skips validation since cached paths are already normalized and relative."""

    def __init__(self, path_str: str):
        self.path = Path(path_str)
