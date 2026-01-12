"""
Main Quicken application and tool execution.

Provides the main Quicken class for managing cached tool execution.
"""

import time
import logging
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from ._cache import RepoPath, QuickenCache
from ._tool_cmd import ToolCmd, ToolCmdFactory


class QuickenLogger(logging.Logger):
    """Logger for Quicken operations."""

    def __init__(self, log_dir: Path):
        """Initialize logger with file handler.
        Args:    log_dir: Directory where log file will be created"""
        super().__init__("Quicken", logging.INFO)

        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "quicken.log"

        # Remove existing handlers to avoid duplicates
        self.handlers.clear()

        # File handler
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.INFO)

        # Format: timestamp - level - message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        self.addHandler(handler)


class Quicken:
    """Main Quicken application."""

    _data_dir = Path.home() / ".quicken"

    def __init__(self, repo_dir: Path, cache_dir: Optional[Path] = None):
        """Initialize Quicken for a specific repository.
        Tools must be configured in ~/.quicken/tools.json (created by installation).
        Args:    repo_dir: Repository root directory (absolute path)
                 cache_dir: Optional cache directory path (defaults to ~/.quicken/cache)"""
        self.repo_dir = repo_dir.absolute()  # Normalize to absolute path
        cache_path = cache_dir if cache_dir else self._data_dir / "cache"
        self.cache = QuickenCache(cache_path)
        self.logger = QuickenLogger(self._data_dir)


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
        tool = ToolCmdFactory.create(
            tool_name, tool_args,
            self.logger, self.cache, optimization, output_args, input_args
        )

        # Try optimization levels: specific level if provided, all levels if None
        cache_entry = None
        for opt_level in tool.get_valid_optimization_levels(optimization):
            tool.optimization = opt_level
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, self.repo_dir, input_args)
            if cache_entry:
                break

        # If no cache hit and optimization was None, default to level 0
        if cache_entry is None and optimization is None and tool.supports_optimization:
            tool.optimization = 0
            modified_args = tool.add_optimization_flags(tool_args)

        if cache_entry:
            returncode = self.cache.restore(cache_entry, self.repo_dir)
            self.logger.info(f"CACHE HIT - file: {source_repo_path}, tool: {tool_name}, "
                           f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                           f"args: {modified_args}, cache_entry: {cache_entry.name}, "
                           f"returncode: {returncode}")
            return returncode

        # Get dependencies from tool
        dependency_repo_paths = tool.get_dependencies(abs_source_file, self.repo_dir)

        # Execute tool and detect output files
        output_files, stdout, stderr, returncode = tool.run(abs_source_file, self.repo_dir)

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
