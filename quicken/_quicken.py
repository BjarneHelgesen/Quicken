"""Quicken API."""

from pathlib import Path
from typing import List, Optional

from ._cache import QuickenCache
from ._logger import QuickenLogger
from ._tool_cmd import ToolCmd
from ._cl_cmd import ClCmd
from ._clang_cmd import ClangCmd
from ._clang_tidy_cmd import ClangTidyCmd
from ._doxygen_cmd import DoxygenCmd
from ._moc_cmd import MocCmd
from ._uic_cmd import UicCmd
from ._type_check import typecheck_methods


@typecheck_methods
class Quicken:
    """Quicken API
    Basic usage: 
    quicken = Quicken(repo)
    tool = quicken.<tool>(command_line_args, [], []) # tool = cl, clang, etc.
    stdout, stderr, returncode = tool(file)
    """

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
        return ClCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir, optimization)

    def clang(self, tool_args: List[str], output_args: List[str], input_args: List[str],
              optimization: Optional[int] = None) -> ToolCmd:
        """Create a reusable clang++ compiler command."""
        return ClangCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir, optimization)

    def clang_tidy(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable clang-tidy command."""
        return ClangTidyCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def doxygen(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable doxygen command."""
        return DoxygenCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def moc(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable Qt MOC (Meta-Object Compiler) command."""
        return MocCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def uic(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> ToolCmd:
        """Create a reusable Qt UIC (User Interface Compiler) command."""
        return UicCmd(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
