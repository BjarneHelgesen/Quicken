"""MSVC cl.exe command wrapper."""

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from ._cmd_tool import CmdTool, PathArg
from ._msvc import MsvcEnv, get_dependencies_showincludes
from ._repo_file import RepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdCl(CmdTool):
    """MSVC cl.exe compiler command."""

    def __init__(self, arguments: List[str], logger, output_args: List[PathArg], input_args: List[PathArg],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("cl", arguments, logger, output_args, input_args, cache, repo_dir)

    def get_execution_env(self) -> Dict | None:
        return MsvcEnv.get()

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        return get_dependencies_showincludes(main_file, repo_dir)

    def get_output_patterns(self, source_file: Path, repo_dir: Path, resolved_output_paths: List[Path] = None) -> List[str]:
        """Return absolute patterns for files MSVC cl will create.
        Uses resolved_output_paths from output_args or defaults based on source stem."""
        patterns = []
        stem = source_file.stem

        # Check for /FA (assembly listing) in tool_args
        generates_asm = any(arg.startswith("/FA") or arg.startswith("-FA") for arg in self.arguments)

        # Use resolved output paths if provided
        if resolved_output_paths:
            for output_path in resolved_output_paths:
                path_str = str(output_path)
                if path_str.endswith("/") or path_str.endswith("\\"):
                    # Directory: add stem.obj in that directory
                    patterns.append(str(output_path / f"{stem}.obj"))
                    if generates_asm:
                        patterns.append(str(output_path / f"{stem}.asm"))
                else:
                    patterns.append(path_str)
        else:
            # Default patterns when no output_args specified
            patterns.append(str(repo_dir / f"{stem}.obj"))
            patterns.append(str(repo_dir / "**" / f"{stem}.obj"))

            if generates_asm:
                patterns.append(str(repo_dir / f"{stem}.asm"))
                patterns.append(str(repo_dir / "**" / f"{stem}.asm"))

            # Check if linking (no /c flag) - may create .exe
            if not any(arg in ('/c', '-c') for arg in self.arguments):
                patterns.append(str(repo_dir / f"{stem}.exe"))
                patterns.append(str(repo_dir / "**" / f"{stem}.exe"))

        return patterns
