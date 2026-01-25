#!/usr/bin/env python3
"""Unit tests for the cleanup tool."""
import json
import os
import shutil
import time
from pathlib import Path

import pytest

from cleanup import CacheCleanup, CleanupCacheEntry, RepoStats, main, format_size, format_age, VERSION


@pytest.fixture
def mock_cache_dir(tmp_path):
    """Create a mock cache directory with test entries."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return cache_dir


@pytest.fixture
def mock_repo1(tmp_path):
    """Platform-agnostic mock repo path."""
    repo = tmp_path / "repo1"
    repo.mkdir()
    return str(repo)


@pytest.fixture
def mock_repo2(tmp_path):
    """Platform-agnostic mock repo path."""
    repo = tmp_path / "repo2"
    repo.mkdir()
    return str(repo)


def create_mock_entry(
    cache_dir: Path,
    folder_name: str,
    entry_name: str,
    repo_dir: str,
    tool_name: str,
    age_days: float = 0,
    file_content: str = "test content",
):
    """Create a mock cache entry with metadata."""
    folder_path = cache_dir / folder_name
    folder_path.mkdir(exist_ok=True)

    entry_dir = folder_path / entry_name
    entry_dir.mkdir()

    # Create metadata.json
    metadata = {
        "cache_key": entry_name,
        "source_file": "test.cpp",
        "tool_name": tool_name,
        "tool_args": ["/c"],
        "main_file_path": "test.cpp",
        "dependencies": [],
        "files": ["test.obj"],
        "stdout": "",
        "stderr": "",
        "returncode": 0,
        "repo_dir": repo_dir,
    }
    metadata_file = entry_dir / "metadata.json"
    metadata_file.write_text(json.dumps(metadata))

    # Create a mock output file
    (entry_dir / "test.obj").write_text(file_content)

    # Set modification time for age
    if age_days > 0:
        old_time = time.time() - (age_days * 86400)
        os.utime(metadata_file, (old_time, old_time))

    return entry_dir


class TestCacheCleanup:
    """Tests for CacheCleanup class."""

    def test_iter_entries_empty_cache(self, mock_cache_dir):
        cleanup = CacheCleanup(mock_cache_dir)
        entries = list(cleanup.iter_entries())
        assert entries == []

    def test_iter_entries_with_entries(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder1", "entry_000002", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        cleanup = CacheCleanup(mock_cache_dir)
        entries = list(cleanup.iter_entries())
        assert len(entries) == 3

    def test_iter_entries_skips_corrupted(self, mock_cache_dir, mock_repo1):
        # Create valid entry
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")

        # Create corrupted entry (invalid JSON)
        folder = mock_cache_dir / "folder2"
        folder.mkdir()
        entry_dir = folder / "entry_000001"
        entry_dir.mkdir()
        (entry_dir / "metadata.json").write_text("not valid json")

        cleanup = CacheCleanup(mock_cache_dir)
        entries = list(cleanup.iter_entries())
        assert len(entries) == 1

    def test_get_stats_groups_by_repo(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder1", "entry_000002", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        cleanup = CacheCleanup(mock_cache_dir)
        stats = cleanup.get_stats()

        assert len(stats) == 2
        assert stats[mock_repo1].entry_count == 2
        assert stats[mock_repo2].entry_count == 1

    def test_find_entries_no_filter(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries()
        assert len(entries) == 2

    def test_find_entries_filter_by_repo(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries(repo=Path(mock_repo1))
        assert len(entries) == 1
        assert entries[0].metadata.repo_dir == mock_repo1

    def test_find_entries_filter_by_tool(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries(tool="cl")
        assert len(entries) == 1
        assert entries[0].metadata.tool_name == "cl"

    def test_find_entries_filter_by_age(self, mock_cache_dir, mock_repo1, mock_repo2):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl", age_days=5)
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link", age_days=15)

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries(older_than_days=10)
        assert len(entries) == 1
        assert entries[0].metadata.tool_name == "link"

    def test_find_entries_combined_filters_and_logic(self, mock_cache_dir, mock_repo1, mock_repo2):
        # Create entries with different combinations
        create_mock_entry(mock_cache_dir, "f1", "entry_000001", mock_repo1, "cl", age_days=20)
        create_mock_entry(mock_cache_dir, "f2", "entry_000001", mock_repo1, "link", age_days=20)
        create_mock_entry(mock_cache_dir, "f3", "entry_000001", mock_repo2, "cl", age_days=20)
        create_mock_entry(mock_cache_dir, "f4", "entry_000001", mock_repo1, "cl", age_days=5)

        cleanup = CacheCleanup(mock_cache_dir)

        # Filter: repo1 AND older than 10 days AND tool=cl
        entries = cleanup.find_entries(
            repo=Path(mock_repo1),
            older_than_days=10,
            tool="cl",
        )
        assert len(entries) == 1
        assert entries[0].metadata.repo_dir == mock_repo1
        assert entries[0].metadata.tool_name == "cl"
        assert entries[0].age_days >= 10

    def test_delete_entries_removes_files(self, mock_cache_dir, mock_repo1):
        entry_dir = create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        assert entry_dir.exists()

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries()
        deleted, failed, deleted_bytes = cleanup.delete_entries(entries)

        assert deleted == 1
        assert failed == 0
        assert deleted_bytes > 0
        assert not entry_dir.exists()

    def test_delete_entries_dry_run(self, mock_cache_dir, mock_repo1):
        entry_dir = create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        assert entry_dir.exists()

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries()
        deleted, failed, deleted_bytes = cleanup.delete_entries(entries, dry_run=True)

        assert deleted == 1
        assert failed == 0
        assert deleted_bytes > 0
        assert entry_dir.exists()  # Should still exist

    def test_delete_entries_cleans_empty_folders(self, mock_cache_dir, mock_repo1):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        folder = mock_cache_dir / "folder1"
        assert folder.exists()

        cleanup = CacheCleanup(mock_cache_dir)
        entries = cleanup.find_entries()
        deleted, failed, deleted_bytes = cleanup.delete_entries(entries)

        assert deleted == 1
        assert failed == 0
        assert not folder.exists()  # Empty folder should be removed


class TestRepoStats:
    """Tests for RepoStats class."""

    def test_empty_stats(self, tmp_path):
        repo = tmp_path / "repo"
        repo.mkdir()
        stats = RepoStats(str(repo))
        assert stats.entry_count == 0
        assert stats.total_size == 0
        assert stats.oldest_days == 0
        assert stats.newest_days == 0


class TestFormatFunctions:
    """Tests for formatting functions."""

    def test_format_size_bytes(self):
        assert format_size(500) == "500 B"

    def test_format_size_kilobytes(self):
        assert format_size(2048) == "2.0 KB"

    def test_format_size_megabytes(self):
        assert format_size(5 * 1024 * 1024) == "5.0 MB"

    def test_format_size_gigabytes(self):
        assert format_size(2 * 1024 * 1024 * 1024) == "2.0 GB"

    def test_format_age_minutes(self):
        assert "minutes ago" in format_age(0.01)

    def test_format_age_hours(self):
        assert "hours ago" in format_age(0.5)

    def test_format_age_days(self):
        assert "days ago" in format_age(5)

    def test_format_age_months(self):
        assert "months ago" in format_age(60)


class TestCLI:
    """Tests for CLI interface."""

    def test_version_flag(self, capsys):
        with pytest.raises(SystemExit) as exc_info:
            main(["--version"])
        assert exc_info.value.code == 0
        captured = capsys.readouterr()
        assert VERSION in captured.out

    def test_no_args_shows_help(self, capsys):
        result = main([])
        assert result == 1

    def test_stats_and_clear_mutually_exclusive(self, capsys):
        result = main(["--stats", "--clear"])
        assert result == 1
        captured = capsys.readouterr()
        assert "Cannot use --stats and --clear together" in captured.out

    def test_dry_run_requires_clear(self, capsys):
        result = main(["--dry-run"])
        assert result == 1
        captured = capsys.readouterr()
        assert "--dry-run requires --clear" in captured.out

    def test_stats_empty_cache(self, mock_cache_dir, capsys):
        result = main(["--stats", "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert "empty" in captured.out.lower()

    def test_stats_with_entries(self, mock_cache_dir, mock_repo1, mock_repo2, capsys):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        result = main(["--stats", "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert mock_repo1 in captured.out
        assert mock_repo2 in captured.out

    def test_clear_all(self, mock_cache_dir, mock_repo1, mock_repo2, capsys):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        result = main(["--clear", "--all", "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert "Deleted 2 entries" in captured.out

    def test_clear_requires_filter_or_all(self, mock_cache_dir, capsys):
        result = main(["--clear", "--cache-dir", str(mock_cache_dir)])
        assert result == 1
        captured = capsys.readouterr()
        assert "--clear requires a filter" in captured.out

    def test_clear_with_repo_filter(self, mock_cache_dir, mock_repo1, mock_repo2, capsys):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")
        create_mock_entry(mock_cache_dir, "folder2", "entry_000001", mock_repo2, "link")

        result = main(["--clear", "--repo", mock_repo1, "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert "Deleted 1 entries" in captured.out

    def test_clear_dry_run(self, mock_cache_dir, mock_repo1, capsys):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")

        result = main(["--clear", "--all", "--dry-run", "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert "Would delete 1 entries" in captured.out

        # Verify entry still exists
        cleanup = CacheCleanup(mock_cache_dir)
        assert len(list(cleanup.iter_entries())) == 1

    def test_clear_no_matches(self, mock_cache_dir, mock_repo1, capsys):
        create_mock_entry(mock_cache_dir, "folder1", "entry_000001", mock_repo1, "cl")

        result = main(["--clear", "--tool", "nonexistent", "--cache-dir", str(mock_cache_dir)])
        assert result == 0
        captured = capsys.readouterr()
        assert "No matching entries found" in captured.out

    def test_older_than_negative_rejected(self, mock_cache_dir, capsys):
        result = main(["--clear", "--older-than", "-5", "--cache-dir", str(mock_cache_dir)])
        assert result == 1
        captured = capsys.readouterr()
        assert "--older-than cannot be negative" in captured.out
