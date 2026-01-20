"""Qt User Interface Compiler (UIC) command wrapper."""

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._repo_file import RepoFile, ValidatedRepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdUic(CmdTool):
    """Qt User Interface Compiler command wrapper.
    UIC reads .ui files (XML from Qt Designer) and generates C++ header files
    (typically ui_*.h)."""

    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("uic", arguments, logger, output_args, input_args, cache, repo_dir)

    def get_execution_env(self) -> Dict | None:
        return None

    def get_output_patterns(self, source_file: Path, repo_dir: Path) -> List[str]:
        """Return absolute patterns for files UIC will create.
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
            patterns.append(str(repo_dir / output_path))
            patterns.append(str(repo_dir / "**" / Path(output_path).name))
        else:
            patterns.append(str(repo_dir / f"ui_{stem}.h"))
            patterns.append(str(repo_dir / "**" / f"ui_{stem}.h"))

        return patterns

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get dependencies for UIC: just the .ui file itself.
        UI files are self-contained XML and don't have external dependencies."""
        return [ValidatedRepoFile(repo_dir, main_file)]
