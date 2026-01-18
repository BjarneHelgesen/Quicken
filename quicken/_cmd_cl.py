"""MSVC cl.exe command wrapper."""

from pathlib import Path
from typing import List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdCl(CmdTool):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("cl", True, arguments, logger, output_args, input_args, cache, repo_dir)

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
