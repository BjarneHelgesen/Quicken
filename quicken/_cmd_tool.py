"""
Tool command wrappers for Quicken.

Provides ToolCmd base class and tool-specific subclasses with
dependency tracking.
"""

import glob
import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, TYPE_CHECKING
from abc import ABC, abstractmethod

from ._repo_file import RepoFile, ValidatedRepoFile
from ._cache import CacheKey
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache

# Type alias for path arguments: (prefix, separator, path)
# Examples:
#   ("/Fo", "", Path("build/out.obj"))     -> /Fobuild/out.obj
#   ("-o", " ", Path("build/out.obj"))     -> -o build/out.obj
#   ("--output", "=", Path("build/out.obj")) -> --output=build/out.obj
PathArg = Tuple[str, str, Path]


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
                 output_args: List[PathArg], input_args: List[PathArg], cache: "QuickenCache", repo_dir: Path):
        self.tool_name = tool_name
        self.arguments = arguments
        self.logger = logger
        self.output_args = output_args  # Output path arguments (not part of cache key)
        self.input_args = input_args  # Input path arguments (in-repo paths part of cache key)
        self.cache = cache
        self.repo_dir = repo_dir
        self._tool_path = None  # Lazy-loaded tool path

    @classmethod
    def _get_config(cls) -> Dict:
        """Load configuration from tools.json (lazy, cached)."""
        if cls._config is None:
            with open(cls._data_dir / "tools.json", 'r', encoding="utf-8") as f:
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

    @abstractmethod
    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get list of dependency paths for caching.
        Args:    main_file: Main file being processed (source file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository root directory
        Returns: List of RepoFile instances for all dependencies"""

    def _resolve_path_args(self, path_args: List[PathArg], cwd: Path) -> Tuple[List[str], List[Path]]:
        """Resolve PathArg tuples to command-line arguments and absolute paths.
        Args:    path_args: List of (prefix, separator, path) tuples
                 cwd: Current working directory for resolving relative paths
        Returns: Tuple of (command_args, resolved_paths)
                 command_args: List of strings to extend command with
                 resolved_paths: List of resolved absolute paths"""
        cmd_args = []
        resolved_paths = []

        for prefix, separator, path in path_args:
            # Resolve relative paths against CWD
            if not path.is_absolute():
                resolved = cwd / path
            else:
                resolved = path
            resolved = Path(os.path.normpath(resolved))
            resolved_paths.append(resolved)

            # Build command argument based on separator
            if separator == " ":
                # Space separator: two separate command-line arguments
                cmd_args.append(prefix)
                cmd_args.append(str(resolved))
            else:
                # Concatenated (empty or other separator like '=')
                cmd_args.append(f"{prefix}{separator}{resolved}")

        return cmd_args, resolved_paths

    def build_execution_command(self, main_file: Path = None,
                                resolved_input_args: List[str] = None,
                                resolved_output_args: List[str] = None) -> List[str]:
        """Build complete command for execution.
        Args:    main_file: Main file path for repo-level tools (e.g., Doxyfile) or source file for file-level tools
                 resolved_input_args: Pre-resolved input arguments (if None, not added)
                 resolved_output_args: Pre-resolved output arguments (if None, not added)
        Returns: Complete command list for subprocess"""
        cmd = [self.tool_path] + self.arguments

        # Add resolved input_args
        if resolved_input_args:
            cmd.extend(resolved_input_args)

        # Add main file before output args (some tools expect source file before -o)
        if main_file:
            cmd.append(str(main_file))

        # Append resolved output_args at the end
        if resolved_output_args:
            cmd.extend(resolved_output_args)

        return cmd

    @abstractmethod
    def get_output_patterns(self, source_file: Path, repo_dir: Path, resolved_output_paths: List[Path] = None) -> List[str]:
        """Return absolute patterns for files this tool will create.
        Patterns can include glob wildcards (*, **, ?).
        Args:    source_file: Path to source file
                 repo_dir: Repository root directory
                 resolved_output_paths: Resolved absolute paths from output_args (optional)
        Returns: List of absolute glob patterns"""

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

    def run(self, repo_file: RepoFile, repo_dir: Path, env: Dict | None = None,
            resolved_input_args: List[str] = None, resolved_output_args: List[str] = None,
            resolved_output_paths: List[Path] = None) -> Tuple[CmdToolRunResult, List[RepoFile]]:
        """Run the tool and detect output files.
        Args:    source_file: RepoFile to file to process (C++ file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository directory (scan location for output files)
                 env: Environment variables for subprocess (None uses current env)
                 resolved_input_args: Pre-resolved input command arguments
                 resolved_output_args: Pre-resolved output command arguments
                 resolved_output_paths: Resolved absolute paths from output_args
        Returns: Tuple of (ToolRunResult, dependencies)"""
        abs_source_file = repo_file.to_absolute_path(repo_dir)
        dependencies = self.get_dependencies(abs_source_file, repo_dir)

        patterns = self.get_output_patterns(abs_source_file, repo_dir, resolved_output_paths)
        files_before = self._get_file_timestamps(patterns)

        cmd = self.build_execution_command(abs_source_file, resolved_input_args, resolved_output_args)

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
        Args:    file: File to process (absolute or CWD-relative path)
        Returns: Tuple of (stdout, stderr, returncode)"""
        cwd = Path.cwd()
        repo_file = ValidatedRepoFile(self.repo_dir, file, cwd=cwd)

        # Resolve path arguments against CWD
        resolved_input_args, _ = self._resolve_path_args(self.input_args, cwd)
        resolved_output_args, resolved_output_paths = self._resolve_path_args(self.output_args, cwd)

        # Return the cached artifacts if found
        cache_key = CacheKey(repo_file, self, self.repo_dir, cwd)
        cache_entry = self.cache.lookup(cache_key, self.repo_dir)
        self.logger.info(f"Cached entry found: {cache_entry}: {repo_file}, tool: {self.tool_name} source:{file}")
        if cache_entry:
            return self.cache.restore(cache_entry, self.repo_dir)

        # No cached artifacts found. Execute the tool and store it in cache if successful
        result, dependencies = self.run(repo_file, self.repo_dir, self.get_execution_env(),
                                        resolved_input_args, resolved_output_args, resolved_output_paths)
        if result.returncode == 0:
            self.cache.store(cache_key, dependencies, result, self.repo_dir)
        return result.stdout, result.stderr, result.returncode
