"""Clang-tidy command wrapper."""

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from ._cmd_tool import CmdTool, PathArg
from ._msvc import get_dependencies_showincludes
from ._repo_file import RepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdClangTidy(CmdTool):
    """Clang-tidy static analyzer command."""

    def __init__(self, arguments: List[str], logger, output_args: List[PathArg], input_args: List[PathArg],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("clang-tidy", arguments, logger, output_args, input_args, cache, repo_dir)

    def get_execution_env(self) -> Dict | None:
        return None

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        return get_dependencies_showincludes(main_file, repo_dir)

    def get_output_patterns(self, _source_file: Path, repo_dir: Path, resolved_output_paths: List[Path] = None) -> List[str]:
        """Return absolute patterns for files clang-tidy will create.
        clang-tidy typically doesn't create files, but can with --export-fixes."""
        patterns = []

        # Use resolved output paths if provided (from --export-fixes output_arg)
        if resolved_output_paths:
            for output_path in resolved_output_paths:
                patterns.append(str(output_path))
                patterns.append(str(output_path.parent / "**" / output_path.name))

        # clang-tidy doesn't create output files in normal operation
        return patterns
