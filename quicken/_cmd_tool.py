"""
Tool command wrappers for Quicken.

Provides ToolCmd base class and tool-specific subclasses with
dependency tracking.
"""

import glob
import json
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, TYPE_CHECKING
from abc import ABC, abstractmethod

from ._repo_file import RepoFile, ValidatedRepoFile
from ._cache import CacheKey
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdToolRunResult:
    """Result of running a tool command."""

    def __init__(self, output_files: List[Path], stdout: str, stderr: str, returncode: int):
        self.output_files = output_files
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@typecheck_methods
class CmdTool(ABC):
    """Base class for tool command wrappers.

    Subclasses define tool-specific behavior for dependency tracking.
    """

    # Shared class attributes for config
    _data_dir = Path.home() / ".quicken"
    _config = None

    def __init__(self, tool_name: str, arguments: List[str], logger,
                 output_args: List[str], input_args: List[str], cache: "QuickenCache", repo_dir: Path):
        self.tool_name = tool_name
        self.arguments = arguments
        self.logger = logger
        self.output_args = output_args  # Output-specific arguments (not part of cache key)
        self.input_args = input_args  # Input-specific arguments (part of cache key)
        self.cache = cache
        self.repo_dir = repo_dir
        self._tool_path = None  # Lazy-loaded tool path

    @classmethod
    def _get_config(cls) -> Dict:
        """Load configuration from tools.json (lazy, cached)."""
        if cls._config is None:
            with open(cls._data_dir / "tools.json", 'r') as f:
                cls._config = json.load(f)
        return cls._config

    @property
    def tool_path(self) -> str:
        """Get the full path to the tool, loading it lazily from config."""
        if self._tool_path is None:
            self._tool_path = self._get_config()[self.tool_name]
        return self._tool_path

    @abstractmethod
    def get_execution_env(self) -> Dict | None:
        """Get environment for tool execution."""
        pass

    @abstractmethod
    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get list of dependency paths for caching.
        Args:    main_file: Main file being processed (source file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository root directory
        Returns: List of RepoFile instances for all dependencies"""
        pass

    def build_execution_command(self, main_file: Path = None) -> List[str]:
        """Build complete command for execution.
        Args:    main_file: Main file path for repo-level tools (e.g., Doxyfile) or source file for file-level tools
        Returns: Complete command list for subprocess"""
        cmd = [self.tool_path] + self.arguments

        # Add input_args (these are part of the cache key). Note that they are joined as a single argument, as the called decides the spacing.
        if self.input_args:
            cmd.extend(self.input_args)

        # Add main file before output args (some tools expect source file before -o)
        if main_file:
            cmd.append(str(main_file))

        # Append output_args at the end (these are not part of the cache key)
        if self.output_args:
            cmd.extend(self.output_args)

        return cmd

    @abstractmethod
    def get_output_patterns(self, source_file: Path, repo_dir: Path) -> List[str]:
        """Return absolute patterns for files this tool will create.
        Patterns can include glob wildcards (*, **, ?).
        Args:    source_file: Path to source file
                 repo_dir: Repository root directory
        Returns: List of absolute glob patterns"""
        pass

    @staticmethod
    def _get_file_timestamps(patterns: List[str]) -> Dict[Path, int]:
        """Get dictionary of file paths to their modification timestamps for files matching patterns.
        Args:    patterns: List of absolute glob patterns (can include wildcards)
        Returns: Dictionary mapping file paths to st_mtime_ns timestamps"""
        file_timestamps = {}
        for pattern in patterns:
            # Use glob.glob which handles absolute paths with wildcards
            for f_str in glob.glob(pattern, recursive=True):
                f = Path(f_str)
                if f.is_file():
                    try:
                        file_timestamps[f] = f.stat().st_mtime_ns
                    except (OSError, FileNotFoundError):
                        pass

        return file_timestamps

    def run(self, repo_file: RepoFile, repo_dir: Path, env: Dict | None = None) -> Tuple[CmdToolRunResult, List[RepoFile]]:
        """Run the tool and detect output files.
        Args:    source_file: RepoFile to file to process (C++ file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository directory (scan location for output files)
                 env: Environment variables for subprocess (None uses current env)
        Returns: Tuple of (ToolRunResult, dependencies)"""
        abs_source_file = repo_file.to_absolute_path(repo_dir)
        dependencies = self.get_dependencies(abs_source_file, repo_dir)

        patterns = self.get_output_patterns(abs_source_file, repo_dir)
        files_before = self._get_file_timestamps(patterns)

        cmd = self.build_execution_command(abs_source_file)

        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            env=env
        )

        files_after = self._get_file_timestamps(patterns)

        # Detect output files: new files OR files with updated timestamps
        output_files = [
            f for f, mtime in files_after.items()
            if f not in files_before or mtime > files_before[f]
        ]

        return CmdToolRunResult(output_files, result.stdout, result.stderr, result.returncode), dependencies

    def __call__(self, file: Path) -> Tuple[str, str, int]:
        """Execute the tool with caching.
        Args:    file: File to process (absolute or relative path)
        Returns: Tuple of (stdout, stderr, returncode)"""
        repo_file = ValidatedRepoFile(self.repo_dir, file)

        # Return the cached artifacts if found
        cache_key = CacheKey(repo_file, self, self.repo_dir)
        cache_entry = self.cache.lookup(cache_key, self.repo_dir)
        self.logger.info(f"Cached entry found: {cache_entry}: {repo_file}, tool: {self.tool_name} source:{file}")
        if cache_entry:
            return self.cache.restore(cache_entry, self.repo_dir)

        # No cached artifacts found. Execute the tool and store it in cache if successful
        result, dependencies = self.run(repo_file, self.repo_dir, self.get_execution_env())
        if result.returncode == 0:
            self.cache.store(cache_key, dependencies, result, self.repo_dir)
        return result.stdout, result.stderr, result.returncode

