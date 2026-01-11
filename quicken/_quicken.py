"""
Main Quicken application and tool execution.

Provides the main Quicken class for managing cached tool execution.
"""

import time
import json
import logging
import os
import subprocess
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ._cache import RepoPath, QuickenCache
from ._tool_cmd import ToolCmd, ToolCmdFactory


class Quicken:
    """Main Quicken application."""
    
    _data_dir = Path.home() / ".quicken"

    def __init__(self, repo_dir: Path, cache_dir: Optional[Path] = None):
        """Initialize Quicken for a specific repository.
        Tools must be configured in ~/.quicken/tools.json (created by installation).
        Args:    repo_dir: Repository root directory (absolute path)
                 cache_dir: Optional cache directory path (defaults to ~/.quicken/cache)"""
        config_path = self._data_dir / "tools.json"
        self.config = self._load_config(config_path)
        self.repo_dir = repo_dir.absolute()  # Normalize to absolute path
        cache_path = cache_dir if cache_dir else self._data_dir / "cache"
        self.cache = QuickenCache(cache_path)
        self._setup_logging()
        # Eagerly fetch and cache MSVC environment (assumes MSVC is installed)
        self._msvc_env = self._get_msvc_environment()

    def _load_config(self, config_path: Path) -> Dict:
        """Load tools configuration."""
        with open(config_path, 'r') as f:
            return json.load(f)

    def _setup_logging(self):
        """Set up logging to file."""
        log_dir = self._data_dir
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "quicken.log"

        # Configure logger
        self.logger = logging.getLogger("Quicken")
        self.logger.setLevel(logging.INFO)

        # Remove existing handlers to avoid duplicates
        self.logger.handlers.clear()

        # File handler
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.INFO)

        # Format: timestamp - level - message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        self.logger.addHandler(handler)

    def _get_msvc_environment(self) -> Dict:
        """Get MSVC environment variables, cached to avoid repeated vcvarsall.bat calls."""
        vcvarsall = ToolCmd.get_tool_path(self.config, "vcvarsall")
        msvc_arch = self.config.get("msvc_arch", "x64")

        # Cache file location
        cache_file = self._data_dir / "msvc_env.json"

        # Try to load from cache
        if cache_file.exists():
            try:
                with open(cache_file, 'r') as f:
                    cached_data = json.load(f)
                    # Verify cache is for same vcvarsall and arch
                    if (cached_data.get("vcvarsall") == vcvarsall and
                        cached_data.get("msvc_arch") == msvc_arch):
                        return cached_data.get("env", {})
            except (json.JSONDecodeError, KeyError):
                # Cache corrupted, will regenerate
                pass

        # Run vcvarsall and capture environment
        cmd = f'"{vcvarsall}" {msvc_arch} >nul && set'
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse environment variables from output
        env = os.environ.copy()
        for line in result.stdout.splitlines():
            if '=' in line:
                key, _, value = line.partition('=')
                env[key] = value

        # Save to cache
        cache_data = {
            "vcvarsall": vcvarsall,
            "msvc_arch": msvc_arch,
            "env": env
        }

        try:
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            with open(cache_file, 'w') as f:
                json.dump(cache_data, f, indent=2)
        except Exception:
            # If caching fails, still return the environment
            pass

        return env

    def _get_file_timestamps(self, directory: Path) -> Dict[Path, int]:
        """Get dictionary of file paths to their modification timestamps.
        Arg:     directory: Directory to scan
        Returns: Dictionary mapping file paths to st_mtime_ns timestamps"""
        if not directory.exists():
            return {}

        file_timestamps = {}
        for f in directory.rglob("*"):
            if f.is_file():
                try:
                    file_timestamps[f] = f.stat().st_mtime_ns
                except (OSError, FileNotFoundError):
                    pass

        return file_timestamps

    def _run_tool(self, tool: ToolCmd, tool_args: List[str], source_file: Path,
                  repo_dir: Path) -> Tuple[List[Path], str, str, int]:
        """Run the specified tool with arguments.
        Args:    tool: ToolCmd instance
                 tool_args: Arguments to pass to tool (already includes optimization flags)
                 source_file: Path to file to process (C++ file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository directory (scan location for output files)
        Returns: Tuple of (output_files, stdout, stderr, returncode)"""
        files_before = self._get_file_timestamps(repo_dir)

        cmd = tool.build_execution_command(source_file)

        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            env=tool.msvc_env if tool.needs_vcvars else None
        )

        files_after = self._get_file_timestamps(repo_dir)

        # Detect output files: new files OR files with updated timestamps
        output_files = [
            f for f, mtime in files_after.items()
            if f not in files_before or mtime > files_before[f]
        ]

        return output_files, result.stdout, result.stderr, result.returncode

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
        tool_path = ToolCmd.get_tool_path(self.config, tool_name)
        tool = ToolCmdFactory.create(
            tool_name, tool_path, tool_args,
            self.logger, self.config, self.cache, self._msvc_env, optimization, output_args, input_args
        )

        if optimization is None:
            cache_entry, modified_args = tool.try_all_optimization_levels(
                tool_name, tool_args, source_repo_path, self.repo_dir
            )
        else:
            modified_args = tool.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, self.repo_dir, input_args)

        if cache_entry:
            returncode = self.cache.restore(cache_entry, self.repo_dir)
            self.logger.info(f"CACHE HIT - file: {source_repo_path}, tool: {tool_name}, "
                           f"Time: {time.perf_counter()-start_time:.3f} seconds, "
                           f"args: {modified_args}, cache_entry: {cache_entry.name}, "
                           f"returncode: {returncode}")
            return returncode

        # Get dependencies from tool
        dependency_repo_paths = tool.get_dependencies(abs_source_file, self.repo_dir)

        output_files, stdout, stderr, returncode = self._run_tool(tool, modified_args, abs_source_file, self.repo_dir)

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
