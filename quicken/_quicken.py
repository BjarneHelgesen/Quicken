"""
Main Quicken application and tool execution.

Provides the main Quicken class for managing cached tool execution.
"""

import sys
from pathlib import Path
from typing import List, Optional

from ._cache import QuickenCache, CacheKey, make_args_repo_relative
from ._logger import QuickenLogger
from ._repo_path import RepoPath
from ._tool_cmd import ToolCmdFactory
from ._type_check import typecheck_methods


@typecheck_methods
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
            output_args: List[str], input_args: List[str], optimization: Optional[int] = None) -> int:
        """Main execution: optimized cache lookup, or get dependencies and run tool.
        Args:    source_file: File to process (absolute or relative path) - C++ file for compilers, Doxyfile for Doxygen
                 tool_name: Tool to run
                 tool_args: Arguments for the tool (part of cache key)
                 optimization: Optimization level (0-3, or None for tools that don't support optimization)
                 output_args: Output-specific arguments (NOT part of cache key, e.g., ['-o', 'output.s'])
                 input_args: Input-specific arguments (part of cache key, paths translated to repo-relative)
        Returns: Tool exit code (integer)"""

        # Convert source_file to RepoPath and validate it's inside repo
        source_repo_path = RepoPath(self.repo_dir, source_file)
        if not source_repo_path:
            raise ValueError(f"Source file {source_file} is outside repository {self.repo_dir}")

        # Convert RepoPath back to absolute path for tool execution
        abs_source_file = source_repo_path.to_absolute_path(self.repo_dir)

        tool = ToolCmdFactory.create(tool_name, tool_args, self.logger, output_args, input_args, optimization)
        modified_args = tool.add_optimization_flags(tool_args)

        # Create cache key 
        repo_relative_input_args = make_args_repo_relative(input_args, self.repo_dir)
        cache_key = CacheKey(source_repo_path, tool_name, modified_args, repo_relative_input_args, self.repo_dir)

        # Try cache lookup (mtime first, then hash)
        cache_entry = self.cache.lookup(cache_key)
        self.logger.info(f"Cached entry found: {cache_entry}: {source_repo_path}, tool: {tool_name} source:{source_file}")
        if cache_entry:
            return self.cache.restore(cache_entry, self.repo_dir)

        # Get dependencies from tool
        dependency_repo_paths = tool.get_dependencies(abs_source_file, self.repo_dir)

        # Execute tool and store artifacts in cache
        result = tool.run(abs_source_file, self.repo_dir)

        print(result.stdout, end='')
        print(result.stderr, end='', file=sys.stderr)

        self.cache.store(cache_key, dependency_repo_paths, result)

        return result.returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
