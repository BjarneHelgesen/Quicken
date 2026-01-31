"""Quicken - Caching for C++ build tools

Quicken provides transparent caching for C++ compilation and analysis tools,
dramatically speeding up repeated builds by caching tool outputs based on
local file dependencies and content hashes.

Example usage:
    from quicken import Quicken, PathArg

    quicken = Quicken(repo_dir=Path.cwd())
    cl = quicken.cl(
        tool_args=["/c", "/W4"],
        output_args=[("/Fo", "", Path("build/main.obj"))],
        input_args=[]
    )
    stdout, stderr, returncode = cl(Path("main.cpp"))
"""

from ._quicken import Quicken
from ._cmd_tool import PathArg

__all__ = ['Quicken', 'PathArg']
