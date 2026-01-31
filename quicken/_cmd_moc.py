"""Qt Meta-Object Compiler (MOC) command wrapper."""

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from ._cmd_tool import CmdTool, PathArg
from ._msvc import get_dependencies_showincludes
from ._repo_file import RepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdMoc(CmdTool):
    """Qt Meta-Object Compiler command wrapper.
    MOC reads C++ header files containing Q_OBJECT macro and generates
    meta-object source code (typically moc_*.cpp files)."""

    def __init__(self, arguments: List[str], logger, output_args: List[PathArg], input_args: List[PathArg],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("moc", arguments, logger, output_args, input_args, cache, repo_dir)

    def get_execution_env(self) -> Dict | None:
        return None

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        return get_dependencies_showincludes(main_file, repo_dir)

    def get_output_patterns(self, source_file: Path, repo_dir: Path, resolved_output_paths: List[Path] = None) -> List[str]:
        """Return absolute patterns for files MOC will create.
        Uses resolved_output_paths from output_args or defaults to moc_<stem>.cpp naming."""
        patterns = []
        stem = source_file.stem

        # Use resolved output paths if provided
        if resolved_output_paths:
            for output_path in resolved_output_paths:
                patterns.append(str(output_path))
                patterns.append(str(output_path.parent / "**" / output_path.name))
        else:
            # Default MOC output naming convention
            patterns.append(str(repo_dir / f"moc_{stem}.cpp"))
            patterns.append(str(repo_dir / "**" / f"moc_{stem}.cpp"))

        return patterns
