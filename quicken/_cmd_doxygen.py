"""Doxygen documentation generator command wrapper."""

from pathlib import Path
from typing import List, TYPE_CHECKING

from ._cmd_tool import CmdTool
from ._repo_file import RepoFile, ValidatedRepoFile
from ._type_check import typecheck_methods

if TYPE_CHECKING:
    from ._cache import QuickenCache


@typecheck_methods
class CmdDoxygen(CmdTool):
    def __init__(self, arguments: List[str], logger, output_args: List[str], input_args: List[str],
                 cache: "QuickenCache", repo_dir: Path):
        super().__init__("doxygen", False, arguments, logger, output_args, input_args, cache, repo_dir)

    def get_output_patterns(self, source_file: Path, repo_dir: Path) -> List[str]:
        """Return patterns for files doxygen will create.
        Parses Doxyfile to find OUTPUT_DIRECTORY and returns patterns for that directory."""
        patterns = []
        doxyfile_path = repo_dir / source_file if not source_file.is_absolute() else source_file

        # Parse Doxyfile for OUTPUT_DIRECTORY
        output_dir = ""
        if doxyfile_path.exists():
            try:
                with open(doxyfile_path, 'r') as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("OUTPUT_DIRECTORY"):
                            # Parse OUTPUT_DIRECTORY = value
                            parts = line.split("=", 1)
                            if len(parts) == 2:
                                output_dir = parts[1].strip().strip('"')
                            break
            except (OSError, UnicodeDecodeError):
                pass

        if output_dir:
            # Add pattern for all files in output directory
            patterns.append(f"{output_dir}/**/*")
        else:
            # Default doxygen output locations
            patterns.append("xml/**/*")
            patterns.append("html/**/*")
            patterns.append("latex/**/*")

        return patterns

    def get_dependencies(self, main_file: Path, repo_dir: Path) -> List[RepoFile]:
        """Get dependencies for Doxygen: Doxyfile + all C++ source/header files.
        Args:    main_file: Path to Doxyfile
                 repo_dir: Repository root directory
        Returns: List of RepoFile instances for Doxyfile and all C++ files"""
        dependencies = [ValidatedRepoFile(repo_dir, main_file)]  # Include Doxyfile itself

        # Add all C++ source and header files in the repo
        for pattern in ['**/*.cpp', '**/*.h', '**/*.hpp']:
            for file_path in repo_dir.glob(pattern):
                try:
                    repo_file = ValidatedRepoFile(repo_dir, file_path)
                    dependencies.append(repo_file)
                except ValueError:
                    pass  # Skip files outside repo

        return dependencies
