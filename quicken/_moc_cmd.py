"""Qt Meta-Object Compiler (MOC) command wrapper."""

from pathlib import Path
from typing import List, TYPE_CHECKING

from ._tool_cmd import ToolCmd
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


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
