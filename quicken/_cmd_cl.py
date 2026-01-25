"""MSVC cl.exe command wrapper."""

from pathlib import Path
from typing import Dict, List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._msvc import MsvcEnv, get_dependencies_showincludes
from ._repo_file import RepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdCl(CmdTool):
    """MSVC cl.exe compiler command."""

    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("cl", arguments, logger, output_args, input_args, cache, repo_dir)

    def get_execution_env(self) -> Dict | None:
        return MsvcEnv.get()

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        return get_dependencies_showincludes(main_file, repo_dir)

    def get_output_patterns(self, source_file: Path, repo_dir: Path) -> List[str]:
        """Return absolute patterns for files MSVC cl will create.
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
                patterns.append(str(repo_dir / f"{fo_path}{stem}.obj"))
            else:
                patterns.append(str(repo_dir / fo_path))
        else:
            patterns.append(str(repo_dir / f"{stem}.obj"))
            patterns.append(str(repo_dir / "**" / f"{stem}.obj"))

        # Add assembly file pattern if /FA is used
        if generates_asm:
            if fo_path and (fo_path.endswith("/") or fo_path.endswith("\\")):
                patterns.append(str(repo_dir / f"{fo_path}{stem}.asm"))
            else:
                patterns.append(str(repo_dir / f"{stem}.asm"))
                patterns.append(str(repo_dir / "**" / f"{stem}.asm"))

        # Add executable pattern if /Fe is used
        if fe_path:
            patterns.append(str(repo_dir / fe_path))
        elif not any(arg in ('/c', '-c') for arg in all_args):
            # No /c flag means linking, so .exe may be created
            patterns.append(str(repo_dir / f"{stem}.exe"))
            patterns.append(str(repo_dir / "**" / f"{stem}.exe"))

        return patterns
