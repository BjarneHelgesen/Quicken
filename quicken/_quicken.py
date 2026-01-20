"""Quicken API."""

from pathlib import Path
from typing import List, Optional

from ._cache import QuickenCache
from ._logger import QuickenLogger
from ._cmd_tool import CmdTool
from ._cmd_cl import CmdCl
from ._cmd_clang import CmdClang
from ._cmd_clang_tidy import CmdClangTidy
from ._cmd_doxygen import CmdDoxygen
from ._cmd_moc import CmdMoc
from ._cmd_uic import CmdUic
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
        Args:    repo_dir: Repository root directory (normalized to absolute path)
                 cache_dir: Optional cache directory path (defaults to ~/.quicken/cache)"""
        self.repo_dir = repo_dir.absolute()
        cache_path = cache_dir if cache_dir else self._data_dir / "cache"
        self.cache = QuickenCache(cache_path)
        self.logger = QuickenLogger(self._data_dir)

    def cl(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable MSVC cl compiler command."""
        return CmdCl(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def clang(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable clang++ compiler command."""
        return CmdClang(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def clang_tidy(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable clang-tidy command."""
        return CmdClangTidy(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def doxygen(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable doxygen command."""
        return CmdDoxygen(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def moc(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable Qt MOC (Meta-Object Compiler) command."""
        return CmdMoc(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def uic(self, tool_args: List[str], output_args: List[str], input_args: List[str]) -> CmdTool:
        """Create a reusable Qt UIC (User Interface Compiler) command."""
        return CmdUic(tool_args, self.logger, output_args, input_args, self.cache, self.repo_dir)

    def clear_cache(self):
        """Clear the entire cache."""
        self.cache.clear()
