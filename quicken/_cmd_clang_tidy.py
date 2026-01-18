"""Clang-tidy command wrapper."""

from pathlib import Path
from typing import List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdClangTidy(CmdTool):
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
