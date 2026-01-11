"""
Tool command wrappers for Quicken.

Provides ToolCmd base class, tool-specific subclasses, and factory for creating
tool command instances with appropriate optimization flags and dependency tracking.
"""

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from abc import ABC, abstractmethod

from ._cache import RepoPath, QuickenCache


class ToolCmd(ABC):
    """Base class for tool command wrappers.

    Subclasses define tool-specific behavior including optimization flags.
    Optimization flags are hardcoded in subclasses, not read from config,
    to ensure consistent behavior across all installations.
    """

    # Class attributes (overridden by subclasses)
    supports_optimization = False
    optimization_flags = []  # e.g., ["/Od", "/O1", "/O2", "/Ox"] for MSVC
    needs_vcvars = False

    def __init__(self, tool_path: str, arguments: List[str], logger, config, cache, msvc_env, optimization=None, output_args=None, input_args=None):
        self.tool_path = tool_path
        self.arguments = arguments
        self.optimization = optimization
        self.config = config
        self.logger = logger
        self.cache = cache
        self.msvc_env = msvc_env  # MSVC environment for /showIncludes
        self.output_args = output_args if output_args is not None else []  # Output-specific arguments (not part of cache key)
        self.input_args = input_args if input_args is not None else []  # Input-specific arguments (part of cache key)

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoPath]:
        """Get list of dependency paths for caching using MSVC /showIncludes.
        Default implementation for C++ tools. Can be overridden by subclasses.
        Args:    main_file: Main file being processed (source file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository root directory
        Returns: List of RepoPath instances for all dependencies"""
        cl_path = ToolCmd.get_tool_path(self.config, "cl")

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        result = subprocess.run(
            [cl_path, '/showIncludes', '/Zs', str(main_file)],
            env=self.msvc_env,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse /showIncludes output
        dependencies = [RepoPath(repo_dir, main_file)]  # Always include the source file itself

        for line in result.stderr.splitlines():  # /showIncludes outputs to stderr
            if line.startswith("Note: including file:"):
                # Extract the file path (after "Note: including file:")
                file_path_str = line.split(":", 2)[2].strip()
                repo_path = RepoPath(repo_dir, file_path_str)
                if repo_path:  # Only include dependencies inside repo
                    dependencies.append(repo_path)

        return dependencies

    @staticmethod
    def get_tool_path(config: Dict, tool_name: str) -> str:
        """Get the full path to a tool from config.
        Args:    config: Configuration dictionary
                 tool_name: Name of the tool
        Returns: Full path to the tool executable"""
        return config[tool_name]

    def get_optimization_flags(self, level: int) -> List[str]:
        """Return optimization flags for the given level.
        Args:    level: Optimization level (0-3)
        Returns: List of flags (may be empty list, or multiple flags for space-separated)"""
        if not self.supports_optimization:
            return []

        if level < 0 or level >= len(self.optimization_flags):
            raise ValueError(f"Invalid optimization level {level}")

        flag = self.optimization_flags[level]

        # Handle space-separated flags (e.g., "-O0 -fno-inline")
        if isinstance(flag, str) and ' ' in flag:
            return flag.split()

        return [flag] if isinstance(flag, str) else flag

    def add_optimization_flags(self, args: List[str]) -> List[str]:
        """Add optimization flags to arguments if optimization is set.
        Args:    args: Original arguments
        Returns: Modified arguments with optimization flags at beginning"""
        if not self.supports_optimization:
            return args

        # Default to O0 if not specified
        opt_level = self.optimization if self.optimization is not None else 0
        opt_flags = self.get_optimization_flags(opt_level)

        return opt_flags + args

    def build_execution_command(self, main_file: Path = None) -> List[str]:
        """Build complete command for execution.
        Args:    main_file: Main file path for repo-level tools (e.g., Doxyfile) or source file for file-level tools
        Returns: Complete command list for subprocess"""
        modified_args = self.add_optimization_flags(self.arguments)
        cmd = [self.tool_path] + modified_args

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

    def try_all_optimization_levels(self, tool_name: str, tool_args: List[str],
                                   source_repo_path: RepoPath, repo_dir: Path) -> Tuple[Optional[Path], List[str]]:
        """Try to find cache hit with any optimization level.
        Args:    tool_name: Name of the tool
                 tool_args: Tool arguments (without source_file/main_file)
                 source_repo_path: RepoPath for source file
                 repo_dir: Repository directory
        Returns: Tuple of (cache_entry, modified_args) or (None, original_args)"""
        if not self.supports_optimization:
            cache_entry = self.cache.lookup(source_repo_path, tool_name, tool_args, repo_dir, self.input_args)
            return cache_entry, tool_args

        for opt_level in range(len(self.optimization_flags)):
            self.optimization = opt_level
            modified_args = self.add_optimization_flags(tool_args)
            cache_entry = self.cache.lookup(source_repo_path, tool_name, modified_args, repo_dir, self.input_args)
            if cache_entry:
                return cache_entry, modified_args

        # No cache hit found - default to optimization level 0
        self.optimization = 0
        modified_args = self.add_optimization_flags(tool_args)
        return None, modified_args


class ClCmd(ToolCmd):
    supports_optimization = True
    optimization_flags = ["/Od", "/O1", "/O2", "/Ox"]
    needs_vcvars = True

class ClangCmd(ToolCmd):
    supports_optimization = True
    optimization_flags = ["-O0", "-O1", "-O2", "-O3"]
    needs_vcvars = False

class ClangTidyCmd(ToolCmd):
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

class DoxygenCmd(ToolCmd):
    supports_optimization = False
    optimization_flags = []
    needs_vcvars = False

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoPath]:
        """Get dependencies for Doxygen: Doxyfile + all C++ source/header files.
        Args:    main_file: Path to Doxyfile
                 repo_dir: Repository root directory
        Returns: List of RepoPath instances for Doxyfile and all C++ files"""
        dependencies = [RepoPath(repo_dir, main_file)]  # Include Doxyfile itself

        # Add all C++ source and header files in the repo
        for pattern in ['**/*.cpp', '**/*.h', '**/*.hpp']:
            for file_path in repo_dir.glob(pattern):
                repo_path = RepoPath(repo_dir, file_path)
                if repo_path:
                    dependencies.append(repo_path)

        return dependencies

class ToolCmdFactory:
    """Factory for creating ToolCmd instances."""

    _registry = {
        "cl": ClCmd,
        "clang++": ClangCmd,
        "clang-tidy": ClangTidyCmd,
        "doxygen": DoxygenCmd,
    }

    @classmethod
    def create(cls, tool_name: str, tool_path: str, arguments: List[str],
               logger, config, cache, msvc_env, optimization=None, output_args=None, input_args=None) -> ToolCmd:
        """Create ToolCmd instance for the given tool name.
        Args:    tool_name: Name of the tool (must be registered)
                 tool_path: Full path to tool executable
                 arguments: Command-line arguments (part of cache key)
                 logger: Logger instance
                 config: Configuration dict
                 cache: QuickenCache instance
                 msvc_env: MSVC environment dict
                 optimization: Optional optimization level
                 output_args: Output-specific arguments (NOT part of cache key)
                 input_args: Input-specific arguments (part of cache key, paths translated to repo-relative)
        Returns: ToolCmd subclass instance
        Raises:  ValueError: If tool_name is not registered"""
        if tool_name not in cls._registry:
            raise ValueError(f"Unsupported tool: {tool_name}")

        tool_class = cls._registry[tool_name]

        return tool_class(tool_path, arguments, logger, config, cache,
                         msvc_env, optimization, output_args, input_args)
