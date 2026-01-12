"""
Main Quicken application and tool execution.

Provides the main Quicken class for managing cached tool execution.
"""

import time
import sys
from pathlib import Path
from typing import List, Optional

from ._cache import QuickenCache
from ._logger import QuickenLogger
from ._repo_path import RepoPath
from ._tool_cmd import ToolCmd, ToolCmdFactory


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

        def log(status: str):
            """Log cache operation with local context. Locals need to be initalized before calling log()"""
            elapsed = time.perf_counter() - start_time
            cache_entry_name = cache_entry.name if cache_entry else "N/A"
            msg = f"{status}: {source_repo_path}, tool: {tool_name}, Time: {elapsed:.3f}s, args: {modified_args}, returncode: {returncode}, cache_entry: {cache_entry_name}"
            self.logger.info(msg)

        # Convert source_file to RepoPath and validate it's inside repo
        source_repo_path = RepoPath(self.repo_dir, source_file)
        if not source_repo_path:
            raise ValueError(f"Source file {source_file} is outside repository {self.repo_dir}")

        # Convert RepoPath back to absolute path for tool execution
        abs_source_file = source_repo_path.toAbsolutePath(self.repo_dir)

        start_time = time.perf_counter()
        tool = ToolCmdFactory.create(tool_name, tool_args, self.logger, optimization, output_args, input_args)

        # Try optimization levels: specific level if provided, all levels if None
        for opt_level in tool.get_valid_optimization_levels(optimization):
            tool.optimization = opt_level
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, self.repo_dir, input_args)
            if cache_entry:
                returncode = self.cache.restore(cache_entry, self.repo_dir)
                log("CACHE HIT")
                return returncode

        # If no cache hit and optimization was None, default to level 0
        if optimization is None and tool.supports_optimization:
            tool.optimization = 0
            modified_args = tool.add_optimization_flags(tool_args)


        # Get dependencies from tool
        dependency_repo_paths = tool.get_dependencies(abs_source_file, self.repo_dir)

        # Execute tool and store artifacts in cache
        output_files, stdout, stderr, returncode = tool.run(abs_source_file, self.repo_dir)

        print(stdout, end='')
        print(stderr, end='', file=sys.stderr)

        self.cache.store(
            source_repo_path, tool_name, modified_args, dependency_repo_paths, output_files,
            stdout, stderr, returncode, self.repo_dir,
            input_args=input_args
        )
        log("CACHE MISS")

        return returncode

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()

    # Convenience methods for common tools
    def cl(self, source_file: Path, tool_args: List[str],
           optimization: int = None, output_args: List[str] = None, input_args: List[str] = []) -> int:
        """Compile with MSVC cl compiler.
        Args:    source_file: C++ source file to compile
                 tool_args: Compiler arguments (part of cache key)
                 optimization: Optimization level (0-3, or None to accept any cached level)
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key)
        Returns: Tool exit code"""
        return self.run(source_file, "cl", tool_args, optimization, output_args, input_args)

    def clang(self, source_file: Path, tool_args: List[str],
              optimization: int = None, output_args: List[str] = None, input_args: List[str] = []) -> int:
        """Compile with clang++ compiler.
        Args:    source_file: C++ source file to compile
                 tool_args: Compiler arguments (part of cache key)
                 optimization: Optimization level (0-3, or None to accept any cached level)
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key)
        Returns: Tool exit code"""
        return self.run(source_file, "clang++", tool_args, optimization, output_args, input_args)

    def clang_tidy(self, source_file: Path, tool_args: List[str],
                   output_args: List[str] = None, input_args: List[str] = []) -> int:
        """Analyze with clang-tidy static analyzer.
        Args:    source_file: C++ source file to analyze
                 tool_args: Analyzer arguments (part of cache key)
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key)
        Returns: Tool exit code"""
        return self.run(source_file, "clang-tidy", tool_args, None, output_args, input_args)

    def doxygen(self, doxyfile: Path, tool_args: List[str] = [],
                output_args: List[str] = None, input_args: List[str] = []) -> int:
        """Generate documentation with Doxygen.
        Args:    doxyfile: Doxyfile configuration file
                 tool_args: Doxygen arguments (part of cache key)
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key)
        Returns: Tool exit code"""
        return self.run(doxyfile, "doxygen", tool_args, None, output_args, input_args)
