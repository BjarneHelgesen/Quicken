"""Quicken - Caching for C++ build tools

Quicken provides transparent caching for C++ compilation and analysis tools,
dramatically speeding up repeated builds by caching tool outputs based on
local file dependencies and content hashes.

Example usage:
    from quicken import Quicken

    quicken = Quicken(repo_dir=Path.cwd())

    # Convenience methods (recommended)
    quicken.cl(source_file=Path("main.cpp"), tool_args=["/c", "/W4"])
    quicken.clang(source_file=Path("main.cpp"), tool_args=["-c", "-Wall"])
    quicken.clang_tidy(source_file=Path("main.cpp"), tool_args=["--checks=*"])
    quicken.doxygen(doxyfile=Path("Doxyfile"))

    # Generic method (for flexibility)
    quicken.run(source_file=Path("main.cpp"), tool_name="cl", tool_args=["/c", "/W4"])
"""

from ._quicken import Quicken

__all__ = ['Quicken']
