"""Clang++ command wrapper."""

from pathlib import Path
from typing import List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdClang(CmdTool):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("clang++", False, arguments, logger, output_args, input_args, cache, repo_dir)

    def get_output_patterns(self, source_file: Path, _repo_dir: Path) -> List[str]:
        """Return patterns for files clang++ will create.
        Parses arguments to find output paths or uses defaults based on source stem."""
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

        # Check for -S (assembly output)
        generates_asm = "-S" in all_args

        # Check for -c (object file output, no linking)
        compile_only = "-c" in all_args

        if output_path:
            patterns.append(output_path)
            patterns.append(f"**/{output_path}")
        elif generates_asm:
            patterns.append(f"{stem}.s")
            patterns.append(f"**/{stem}.s")
        elif compile_only:
            patterns.append(f"{stem}.o")
            patterns.append(f"**/{stem}.o")
        else:
            # Linking, creates executable (a.out or stem)
            patterns.append(f"{stem}")
            patterns.append("a.out")
            patterns.append(f"**/{stem}")
            patterns.append("**/a.out")

        return patterns
