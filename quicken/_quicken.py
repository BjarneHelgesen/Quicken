"""
Main Quicken application and tool execution.

Provides the main Quicken class for managing cached tool execution.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from ._cache import QuickenCache, CacheKey
from ._logger import QuickenLogger
from ._repo_path import RepoPath
from ._tool_cmd import ToolCmd, ClCmd, ClangCmd, ClangTidyCmd, DoxygenCmd
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

    def cl(self, tool_args: List[str], output_args: List[str], input_args: List[str],
           optimization: Optional[int] = None) -> ToolCmd:
        """Create a reusable MSVC cl compiler command."""
        return ClCmd("cl", tool_args, self.logger, output_args, input_args, optimization)

    def clang(self, tool_args: List[str], output_args: List[str], input_args: List[str],
              optimization: Optional[int] = None) -> ToolCmd:
        """Create a reusable clang++ compiler command."""
        return ClangCmd("clang++", tool_args, self.logger, output_args, input_args, optimization)

    def clang_tidy(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable clang-tidy command."""
        return ClangTidyCmd("clang-tidy", tool_args, self.logger, output_args, input_args, None)

    def doxygen(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable doxygen command."""
        return DoxygenCmd("doxygen", tool_args, self.logger, output_args, input_args, None)

    def run(self, file: Path, tool_cmd: ToolCmd) -> Tuple[str, str, int]:
        """Main execution: optimized cache lookup, or get dependencies and run tool.
        Args:    file: File to process (absolute or relative path) - C++ file for compilers, Doxyfile for Doxygen
                 tool_cmd: Tool command created by cl(), clang(), clang_tidy(), or doxygen()
        Returns: Tuple of (stdout, stderr, returncode)"""

        # Store the file path relative to the repo
        repo_file = RepoPath(self.repo_dir, file)

        # Fast path: Look up the build artifacts in the cache and return it
        cache_key = CacheKey(repo_file, tool_cmd, self.repo_dir)
        cache_entry = self.cache.lookup(cache_key, self.repo_dir)
        self.logger.info(f"Cached entry found: {cache_entry}: {repo_file}, tool: {tool_cmd.tool_name} source:{file}")
        if cache_entry:
            return self.cache.restore(cache_entry, self.repo_dir)

        # Slow path: cache lookup has failed, so we need to run the tool and update the cache.
        result, dependencies = tool_cmd.run(repo_file, self.repo_dir)
        if result.returncode == 0:
            self.cache.store(cache_key, dependencies, result, self.repo_dir)
        return result.stdout, result.stderr, result.returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
