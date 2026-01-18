"""
Tool command wrappers for Quicken.

Provides ToolCmd base class and tool-specific subclasses with appropriate
optimization flags and dependency tracking.
"""

import json
import os
import subprocess
from pathlib import Path
from typing import Dict, List, Tuple, TYPE_CHECKING
from abc import ABC

from ._repo_file import RepoFile, ValidatedRepoFile
from ._cache import CacheKey
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class ToolRunResult:
    """Result of running a tool command."""

    def __init__(self, output_files: List[Path], stdout: str, stderr: str, returncode: int):
        self.output_files = output_files
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@typecheck_methods
class ToolCmd(ABC):
    """Base class for tool command wrappers.

    Subclasses define tool-specific behavior including optimization flags.
    Optimization flags are hardcoded in subclasses, not read from config,
    to ensure consistent behavior across all installations.
    """

    # Shared class attributes for config
    _data_dir = Path.home() / ".quicken"
    _config = None

    def __init__(self, tool_name: str, supports_optimization: bool, optimization_flags: List[str],
                 needs_vcvars: bool, arguments: List[str], logger, output_args: List[str],
                 input_args: List[str], cache: "QuickenCache", repo_dir: Path, optimization=None):
        self.tool_name = tool_name
        self.supports_optimization = supports_optimization
        self.optimization_flags = optimization_flags
        self.needs_vcvars = needs_vcvars
        self.arguments = arguments
        self.optimization = optimization
        self.logger = logger
        self.output_args = output_args  # Output-specific arguments (not part of cache key)
        self.input_args = input_args  # Input-specific arguments (part of cache key)
        self.cache = cache
        self.repo_dir = repo_dir
        self._tool_path = None  # Lazy-loaded tool path
        self._msvc_env = None  # Lazy-loaded MSVC environment

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

    @property
    def msvc_env(self) -> Dict:
        """Get MSVC environment, loading it lazily when first accessed."""
        if self._msvc_env is None:
            self._msvc_env = ToolCmd._get_msvc_environment()
        return self._msvc_env

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get list of dependency paths for caching using MSVC /showIncludes.
        Default implementation for C++ tools. Can be overridden by subclasses.
        Args:    main_file: Main file being processed (source file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository root directory
        Returns: List of RepoFile instances for all dependencies"""
        cl_path = self._get_config()["cl"]

        # Run cl with /showIncludes and /Zs (syntax check only, no codegen)
        result = subprocess.run(
            [cl_path, '/showIncludes', '/Zs', str(main_file)],
            env=self.msvc_env,
            capture_output=True,
            text=True,
            check=False
        )

        # Parse /showIncludes output
        dependencies = [ValidatedRepoFile(repo_dir, main_file)]  # Always include the source file itself

        for line in result.stderr.splitlines():  # /showIncludes outputs to stderr
            if line.startswith("Note: including file:"):
                # Extract the file path (after "Note: including file:")
                file_path_str = line.split(":", 2)[2].strip()
                try:
                    repo_file = ValidatedRepoFile(repo_dir, Path(file_path_str))
                    dependencies.append(repo_file)
                except ValueError:
                    pass  # Skip dependencies outside repo (e.g., system headers)

        return dependencies

    @classmethod
    def _get_msvc_environment(cls) -> Dict:
        """Get MSVC environment variables, cached to avoid repeated vcvarsall.bat calls."""
        config = cls._get_config()
        vcvarsall = config["vcvarsall"]
        msvc_arch = config.get("msvc_arch", "x64")

        # Cache file location
        cache_file = cls._data_dir / "msvc_env.json"

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

    def get_output_patterns(self, _source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files this tool will create.
        Patterns are relative to repo_dir and can use glob wildcards.
        Args:    _source_file: Path to source file (used by subclasses)
                 _repo_dir: Repository root directory (used by subclasses)
        Returns: List of glob patterns (relative to repo_dir)"""
        return ["**/*"]  # Default: scan everything (override in subclasses)

    @staticmethod
    def _get_file_timestamps(directory: Path, patterns: List[str]) -> Dict[Path, int]:
        """Get dictionary of file paths to their modification timestamps for files matching patterns.
        Args:    directory: Directory to scan
                 patterns: List of glob patterns (relative to directory) or absolute paths
        Returns: Dictionary mapping file paths to st_mtime_ns timestamps"""
        if not directory.exists():
            return {}

        file_timestamps = {}
        for pattern in patterns:
            pattern_path = Path(pattern)
            if pattern_path.is_absolute():
                # Handle absolute path directly
                if pattern_path.is_file():
                    try:
                        file_timestamps[pattern_path] = pattern_path.stat().st_mtime_ns
                    except (OSError, FileNotFoundError):
                        pass
            else:
                # Relative pattern - use glob
                for f in directory.glob(pattern):
                    if f.is_file():
                        try:
                            file_timestamps[f] = f.stat().st_mtime_ns
                        except (OSError, FileNotFoundError):
                            pass

        return file_timestamps

    def run(self, repo_file: RepoFile, repo_dir: Path) -> Tuple[ToolRunResult, List[RepoFile]]:
        """Run the tool and detect output files.
        Args:    source_file: RepoFile to file to process (C++ file for compilers, Doxyfile for Doxygen)
                 repo_dir: Repository directory (scan location for output files)
        Returns: Tuple of (ToolRunResult, dependencies)"""
        abs_source_file = repo_file.to_absolute_path(repo_dir)
        dependencies = self.get_dependencies(abs_source_file, repo_dir)

        patterns = self.get_output_patterns(abs_source_file, repo_dir)
        files_before = self._get_file_timestamps(repo_dir, patterns)

        cmd = self.build_execution_command(abs_source_file)

        result = subprocess.run(
            cmd,
            cwd=repo_dir,
            capture_output=True,
            text=True,
            env=self.msvc_env if self.needs_vcvars else None
        )

        files_after = self._get_file_timestamps(repo_dir, patterns)

        # Detect output files: new files OR files with updated timestamps
        output_files = [
            f for f, mtime in files_after.items()
            if f not in files_before or mtime > files_before[f]
        ]

        return ToolRunResult(output_files, result.stdout, result.stderr, result.returncode), dependencies

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
        result, dependencies = self.run(repo_file, self.repo_dir)
        if result.returncode == 0:
            self.cache.store(cache_key, dependencies, result, self.repo_dir)
        return result.stdout, result.stderr, result.returncode


@typecheck_methods
class ClCmd(ToolCmd):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path, optimization: int = None):
        super().__init__("cl", True, ["/Od", "/O1", "/O2", "/Ox"], True,
                         arguments, logger, output_args, input_args, cache, repo_dir, optimization)

    def get_output_patterns(self, source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files MSVC cl will create.
        Parses arguments to find output paths or uses defaults based on source stem."""
        patterns = []
        stem = source_file.stem
        all_args = self.arguments + self.output_args

        # Check for /Fo (object file output path)
        fo_path = None
        for arg in all_args:
            if arg.startswith("/Fo") or arg.startswith("-Fo"):
                fo_path = arg[3:]
                break

        # Check for /FA (assembly listing)
        generates_asm = any(arg.startswith("/FA") or arg.startswith("-FA") for arg in all_args)

        # Check for /Fe (executable output)
        fe_path = None
        for arg in all_args:
            if arg.startswith("/Fe") or arg.startswith("-Fe"):
                fe_path = arg[3:]
                break

        # Add object file pattern
        if fo_path:
            # If /Fo specifies a directory, add stem.obj in that directory
            if fo_path.endswith("/") or fo_path.endswith("\\"):
                patterns.append(f"{fo_path}{stem}.obj")
            else:
                patterns.append(fo_path)
        else:
            patterns.append(f"{stem}.obj")
            patterns.append(f"**/{stem}.obj")

        # Add assembly file pattern if /FA is used
        if generates_asm:
            if fo_path and (fo_path.endswith("/") or fo_path.endswith("\\")):
                patterns.append(f"{fo_path}{stem}.asm")
            else:
                patterns.append(f"{stem}.asm")
                patterns.append(f"**/{stem}.asm")

        # Add executable pattern if /Fe is used
        if fe_path:
            patterns.append(fe_path)
        elif not any(arg == "/c" or arg == "-c" for arg in all_args):
            # No /c flag means linking, so .exe may be created
            patterns.append(f"{stem}.exe")
            patterns.append(f"**/{stem}.exe")

        return patterns

@typecheck_methods
class ClangCmd(ToolCmd):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path, optimization: int = None):
        super().__init__("clang++", True, ["-O0", "-O1", "-O2", "-O3"], False,
                         arguments, logger, output_args, input_args, cache, repo_dir, optimization)

    def get_output_patterns(self, source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files clang++ will create.
        Parses arguments to find output paths or uses defaults based on source stem."""
        patterns = []
        stem = source_file.stem
        all_args = self.arguments + self.output_args

        # Check for -o (explicit output path)
        output_path = None
        for i, arg in enumerate(all_args):
            if arg == "-o" and i + 1 < len(all_args):
                output_path = all_args[i + 1]
                break
            if arg.startswith("-o"):
                output_path = arg[2:]
                break

        # Check for -S (assembly output)
        generates_asm = "-S" in all_args

        # Check for -c (object file output, no linking)
        compile_only = "-c" in all_args

        if output_path:
            patterns.append(output_path)
            patterns.append(f"**/{output_path}")
        elif generates_asm:
            patterns.append(f"{stem}.s")
            patterns.append(f"**/{stem}.s")
        elif compile_only:
            patterns.append(f"{stem}.o")
            patterns.append(f"**/{stem}.o")
        else:
            # Linking, creates executable (a.out or stem)
            patterns.append(f"{stem}")
            patterns.append("a.out")
            patterns.append(f"**/{stem}")
            patterns.append("**/a.out")

        return patterns

@typecheck_methods
class ClangTidyCmd(ToolCmd):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("clang-tidy", False, [], False,
                         arguments, logger, output_args, input_args, cache, repo_dir, None)

    def get_output_patterns(self, _source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files clang-tidy will create.
        clang-tidy typically doesn't create files, but can with --export-fixes."""
        patterns = []
        all_args = self.arguments + self.output_args

        # Check for --export-fixes=<file>
        for arg in all_args:
            if arg.startswith("--export-fixes="):
                fixes_file = arg[len("--export-fixes="):]
                patterns.append(fixes_file)
                patterns.append(f"**/{fixes_file}")
                break

        # clang-tidy doesn't create output files in normal operation
        # Return empty list if no --export-fixes found
        return patterns

@typecheck_methods
class MocCmd(ToolCmd):
    """Qt Meta-Object Compiler command wrapper.
    MOC reads C++ header files containing Q_OBJECT macro and generates
    meta-object source code (typically moc_*.cpp files)."""

    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("moc", False, [], False,
                         arguments, logger, output_args, input_args, cache, repo_dir, None)

    def get_output_patterns(self, source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files MOC will create.
        Parses -o argument or defaults to moc_<stem>.cpp naming."""
        patterns = []
        stem = source_file.stem
        all_args = self.arguments + self.output_args

        # Check for -o (explicit output path)
        output_path = None
        for i, arg in enumerate(all_args):
            if arg == "-o" and i + 1 < len(all_args):
                output_path = all_args[i + 1]
                break
            if arg.startswith("-o"):
                output_path = arg[2:]
                break

        if output_path:
            patterns.append(output_path)
            patterns.append(f"**/{Path(output_path).name}")
        else:
            # Default MOC output naming convention
            patterns.append(f"moc_{stem}.cpp")
            patterns.append(f"**/moc_{stem}.cpp")

        return patterns


@typecheck_methods
class UicCmd(ToolCmd):
    """Qt User Interface Compiler command wrapper.
    UIC reads .ui files (XML from Qt Designer) and generates C++ header files
    (typically ui_*.h)."""

    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("uic", False, [], False,
                         arguments, logger, output_args, input_args, cache, repo_dir, None)

    def get_output_patterns(self, source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files UIC will create.
        Parses -o/--output argument or defaults to ui_<stem>.h naming."""
        patterns = []
        stem = source_file.stem
        all_args = self.arguments + self.output_args

        # Check for -o or --output (explicit output path)
        output_path = None
        for i, arg in enumerate(all_args):
            if (arg == "-o" or arg == "--output") and i + 1 < len(all_args):
                output_path = all_args[i + 1]
                break
            if arg.startswith("-o"):
                output_path = arg[2:]
                break
            if arg.startswith("--output="):
                output_path = arg[9:]
                break

        if output_path:
            patterns.append(output_path)
            patterns.append(f"**/{Path(output_path).name}")
        else:
            patterns.append(f"ui_{stem}.h")
            patterns.append(f"**/ui_{stem}.h")

        return patterns

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get dependencies for UIC: just the .ui file itself.
        UI files are self-contained XML and don't have external dependencies."""
        return [ValidatedRepoFile(repo_dir, main_file)]


@typecheck_methods
class DoxygenCmd(ToolCmd):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("doxygen", False, [], False,
                         arguments, logger, output_args, input_args, cache, repo_dir, None)

    def get_output_patterns(self, source_file: Path, repo_dir: Path) -> List[str]:
        """Return patterns for files doxygen will create.
        Parses Doxyfile to find OUTPUT_DIRECTORY and returns patterns for that directory."""
        patterns = []
        doxyfile_path = repo_dir / source_file if not source_file.is_absolute() else source_file

        # Parse Doxyfile for OUTPUT_DIRECTORY
        output_dir = ""
        if doxyfile_path.exists():
            try:
                with open(doxyfile_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OUTPUT_DIRECTORY"):
                            # Parse OUTPUT_DIRECTORY = value
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                output_dir = parts[1].strip().strip('"')
                            break
            except (OSError, UnicodeDecodeError):
                pass

        if output_dir:
            # Add pattern for all files in output directory
            patterns.append(f"{output_dir}/**/*")
        else:
            # Default doxygen output locations
            patterns.append("xml/**/*")
            patterns.append("html/**/*")
            patterns.append("latex/**/*")

        return patterns

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get dependencies for Doxygen: Doxyfile + all C++ source/header files.
        Args:    main_file: Path to Doxyfile
                 repo_dir: Repository root directory
        Returns: List of RepoFile instances for Doxyfile and all C++ files"""
        dependencies = [ValidatedRepoFile(repo_dir, main_file)]  # Include Doxyfile itself

        # Add all C++ source and header files in the repo
        for pattern in ['**/*.cpp', '**/*.h', '**/*.hpp']:
            for file_path in repo_dir.glob(pattern):
                try:
                    repo_file = ValidatedRepoFile(repo_dir, file_path)
                    dependencies.append(repo_file)
                except ValueError:
                    pass  # Skip files outside repo

        return dependencies

