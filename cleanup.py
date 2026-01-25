#!/usr/bin/env python3
"""
Quicken Cache Cleanup Tool

Standalone CLI for managing the Quicken cache. Can be compiled to executable with Nuitka.

Usage:
    python cleanup.py --stats                              # Show per-repo statistics
    python cleanup.py --clear --all                        # Delete entire cache
    python cleanup.py --clear --repo .                     # Delete cache for current repo
    python cleanup.py --clear --older-than 30              # Delete entries older than 30 days
    python cleanup.py --clear --repo . --older-than 30     # Combine filters (AND logic)
    python cleanup.py --clear --tool cl                    # Delete entries for specific tool
    python cleanup.py --clear --all --dry-run              # Preview what would be deleted
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Dict, Iterator, List, Optional, Set, Tuple

from quicken._cache import CacheMetadata


DEFAULT_CACHE_DIR = Path.home() / ".quicken" / "cache"
VERSION = "1.0.0"


class CleanupCacheEntry:
    """Represents a single cache entry with its metadata."""

    def __init__(self, entry_dir: Path, metadata: CacheMetadata, age_days: float):
        self.entry_dir = entry_dir
        self.metadata = metadata
        self.age_days = age_days
        self.size_bytes = self._calculate_size()

    def _calculate_size(self) -> int:
        total = 0
        for item in self.entry_dir.rglob("*"):
            if item.is_file():
                try:
                    total += item.stat().st_size
                except OSError:
                    pass
        return total


class RepoStats:
    """Statistics for a single repository."""

    def __init__(self, repo_dir: str):
        self.repo_dir = repo_dir
        self.entries: List[CleanupCacheEntry] = []

    def add_entry(self, entry: CleanupCacheEntry):
        self.entries.append(entry)

    @property
    def entry_count(self) -> int:
        return len(self.entries)

    @property
    def total_size(self) -> int:
        return sum(e.size_bytes for e in self.entries)

    @property
    def oldest_days(self) -> float:
        if not self.entries:
            return 0
        return max(e.age_days for e in self.entries)

    @property
    def newest_days(self) -> float:
        if not self.entries:
            return 0
        return min(e.age_days for e in self.entries)


class CacheCleanup:
    """Manages cache cleanup operations."""

    def __init__(self, cache_dir: Optional[Path] = None):
        self.cache_dir = cache_dir or DEFAULT_CACHE_DIR

    def iter_entries(self) -> Iterator[CleanupCacheEntry]:
        """Iterate over all cache entries, yielding CleanupCacheEntry objects."""
        if not self.cache_dir.exists():
            return

        now = time.time()

        for folder in self.cache_dir.iterdir():
            if not folder.is_dir():
                continue

            # Skip lock files
            if folder.name == ".lock":
                continue

            # Find entry directories
            for entry_dir in folder.iterdir():
                if not entry_dir.is_dir():
                    continue
                if not entry_dir.name.startswith("entry_"):
                    continue

                metadata_file = entry_dir / "metadata.json"
                if not metadata_file.exists():
                    continue

                try:
                    # Use metadata file mtime for age calculation
                    mtime = metadata_file.stat().st_mtime
                    age_days = (now - mtime) / 86400

                    metadata = CacheMetadata.from_file(metadata_file, Path("."))
                    yield CleanupCacheEntry(entry_dir, metadata, age_days)
                except (OSError, ValueError, KeyError) as e:
                    print(f"Warning: Skipping corrupted cache entry {entry_dir}: {e}", file=sys.stderr)
                    continue

    def get_stats(self) -> Dict[str, RepoStats]:
        """Get statistics grouped by repository."""
        stats: Dict[str, RepoStats] = {}

        for entry in self.iter_entries():
            repo_dir = entry.metadata.repo_dir
            if repo_dir not in stats:
                stats[repo_dir] = RepoStats(repo_dir)
            stats[repo_dir].add_entry(entry)

        return stats

    def find_entries(
        self,
        repo: Optional[Path] = None,
        older_than_days: Optional[float] = None,
        tool: Optional[str] = None,
    ) -> List[CleanupCacheEntry]:
        """Find entries matching all specified filters (AND logic)."""
        matches = []

        # Normalize repo path for comparison
        normalized_repo = None
        if repo is not None:
            try:
                normalized_repo = str(repo.resolve()).lower()
            except OSError:
                normalized_repo = str(repo).lower()

        for entry in self.iter_entries():
            # Filter: repo
            if normalized_repo is not None:
                entry_repo = entry.metadata.repo_dir.lower()
                if entry_repo != normalized_repo:
                    continue

            # Filter: older_than_days
            if older_than_days is not None:
                if entry.age_days < older_than_days:
                    continue

            # Filter: tool
            if tool is not None:
                if entry.metadata.tool_name != tool:
                    continue

            matches.append(entry)

        return matches

    def delete_entries(self, entries: List[CleanupCacheEntry], dry_run: bool = False) -> Tuple[int, int, int]:
        """Delete specified entries. Returns (deleted_count, failed_count, deleted_bytes)."""
        deleted = 0
        failed = 0
        deleted_bytes = 0

        # Track which entries were deleted per compound folder
        deleted_by_folder: Dict[Path, Set[str]] = {}

        for entry in entries:
            if dry_run:
                deleted += 1
                deleted_bytes += entry.size_bytes
            else:
                try:
                    shutil.rmtree(entry.entry_dir)
                    deleted += 1
                    deleted_bytes += entry.size_bytes

                    # Track deletion for folder index update
                    compound_folder = entry.entry_dir.parent
                    entry_key = entry.entry_dir.name
                    if compound_folder not in deleted_by_folder:
                        deleted_by_folder[compound_folder] = set()
                    deleted_by_folder[compound_folder].add(entry_key)
                except OSError:
                    failed += 1

        # Update folder indexes to remove deleted entries
        if not dry_run:
            for compound_folder, deleted_keys in deleted_by_folder.items():
                self._update_folder_index(compound_folder, deleted_keys)

        # Clean up empty compound folders
        if not dry_run and self.cache_dir.exists():
            for folder in self.cache_dir.iterdir():
                if not folder.is_dir():
                    continue
                # Check if folder is empty (only has lock file or folder_index.json)
                remaining = [f for f in folder.iterdir()
                             if f.name not in (".lock", "folder_index.json")]
                if not remaining:
                    try:
                        shutil.rmtree(folder)
                    except OSError:
                        pass

        return deleted, failed, deleted_bytes

    def _update_folder_index(self, compound_folder: Path, deleted_keys: Set[str]):
        """Update folder_index.json to remove deleted entries."""
        index_file = compound_folder / "folder_index.json"
        if not index_file.exists():
            return

        try:
            with open(index_file, 'r') as f:
                data = json.load(f)

            # Filter out deleted entries
            data["entries"] = [e for e in data["entries"] if e["cache_key"] not in deleted_keys]

            with open(index_file, 'w') as f:
                json.dump(data, f, indent=2)
        except (OSError, json.JSONDecodeError, KeyError):
            # If we can't update the index, continue gracefully
            # The cache lookup handles missing entries
            pass


def format_size(size_bytes: int) -> str:
    """Format byte size as human-readable string."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    if size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    return f"{size_bytes / (1024 * 1024 * 1024):.1f} GB"


def format_age(days: float) -> str:
    """Format age as human-readable string."""
    if days < 1:
        hours = days * 24
        if hours < 1:
            return f"{int(hours * 60)} minutes ago"
        return f"{int(hours)} hours ago"
    if days < 30:
        return f"{int(days)} days ago"
    return f"{int(days / 30)} months ago"


def cmd_stats(cleanup: CacheCleanup) -> int:
    """Show per-repo statistics."""
    stats = cleanup.get_stats()

    if not stats:
        print("Cache is empty.")
        return 0

    print("Quicken Cache Statistics")
    print("=" * 60)
    print()

    total_entries = 0
    total_size = 0

    for repo_dir, repo_stats in sorted(stats.items()):
        print(repo_dir)
        print(f"  Entries: {repo_stats.entry_count}")
        print(f"  Size: {format_size(repo_stats.total_size)}")
        print(f"  Oldest: {format_age(repo_stats.oldest_days)}")
        print(f"  Newest: {format_age(repo_stats.newest_days)}")
        print()

        total_entries += repo_stats.entry_count
        total_size += repo_stats.total_size

    print("-" * 60)
    print(f"Total: {total_entries} entries, {format_size(total_size)}")

    return 0


def cmd_clear(
    cleanup: CacheCleanup,
    repo: Optional[Path],
    older_than_days: Optional[float],
    tool: Optional[str],
    dry_run: bool,
) -> int:
    """Clear matching cache entries."""
    # Find matching entries
    entries = cleanup.find_entries(
        repo=repo,
        older_than_days=older_than_days,
        tool=tool,
    )

    if not entries:
        print("No matching entries found.")
        return 0

    total_size = sum(e.size_bytes for e in entries)

    if dry_run:
        print(f"Would delete {len(entries)} entries ({format_size(total_size)})")
        print()
        # Group by repo for display
        by_repo: Dict[str, List[CleanupCacheEntry]] = {}
        for entry in entries:
            repo_dir = entry.metadata.repo_dir
            if repo_dir not in by_repo:
                by_repo[repo_dir] = []
            by_repo[repo_dir].append(entry)

        for repo_dir, repo_entries in sorted(by_repo.items()):
            print(f"{repo_dir}: {len(repo_entries)} entries")
    else:
        deleted, failed, deleted_bytes = cleanup.delete_entries(entries)
        print(f"Deleted {deleted} entries ({format_size(deleted_bytes)})")
        if failed > 0:
            print(f"Warning: {failed} entries could not be deleted (permission denied or in use)")

    return 0


def main(args: List[str] = None) -> int:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Quicken Cache Cleanup Tool", formatter_class=argparse.RawDescriptionHelpFormatter)

    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    parser.add_argument("--stats",   action="store_true", help="Show per-repo cache statistics")
    parser.add_argument("--clear",   action="store_true", help="Delete matching cache entries")
    parser.add_argument("--all",     action="store_true", help="Delete all cache entries (requires --clear)")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be deleted without deleting")

    parser.add_argument("--repo",       type=str,  metavar="PATH", help="Filter: entries for this repository (use . for current directory)")
    parser.add_argument("--older-than", type=float,metavar="DAYS", help="Filter: entries older than N days")
    parser.add_argument("--tool",       type=str,  metavar="NAME", help="Filter: entries for specific tool (e.g., cl, link)")
    parser.add_argument("--cache-dir",  type=str,  metavar="PATH", help=f"Cache directory (default: {DEFAULT_CACHE_DIR})")

    parsed = parser.parse_args(args)

    # Validate arguments
    if parsed.stats and parsed.clear:
        print("Error: Cannot use --stats and --clear together.")
        return 1

    if parsed.dry_run and not parsed.clear:
        print("Error: --dry-run requires --clear.")
        return 1

    if parsed.all and not parsed.clear:
        print("Error: --all requires --clear.")
        return 1

    if parsed.clear and not (parsed.all or parsed.repo or parsed.older_than or parsed.tool):
        print("Error: --clear requires a filter (--repo, --older-than, --tool) or --all.")
        return 1

    if parsed.older_than is not None and parsed.older_than < 0:
        print("Error: --older-than cannot be negative.")
        return 1

    if not parsed.stats and not parsed.clear:
        parser.print_help()
        return 1

    # Parse repo path
    repo = None
    if parsed.repo:
        repo = Path(parsed.repo)
        if parsed.repo == ".":
            repo = Path.cwd()

    # Create cleanup instance
    cache_dir = Path(parsed.cache_dir) if parsed.cache_dir else None
    cleanup = CacheCleanup(cache_dir)

    # Execute command
    if parsed.stats:
        return cmd_stats(cleanup)
    elif parsed.clear:
        return cmd_clear(
            cleanup,
            repo=repo,
            older_than_days=parsed.older_than,
            tool=parsed.tool,
            dry_run=parsed.dry_run,
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
