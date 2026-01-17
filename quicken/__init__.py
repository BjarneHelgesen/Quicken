"""Quicken - Caching for C++ build tools

Quicken provides transparent caching for C++ compilation and analysis tools,
dramatically speeding up repeated builds by caching tool outputs based on
local file dependencies and content hashes.

Example usage:
    from quicken import Quicken

    quicken = Quicken(repo_dir=Path.cwd())
    quicken.run(source_file=Path("main.cpp"), tool_name="cl",
                tool_args=["/c", "/W4"], output_args=[], input_args=[])
"""

from ._quicken import Quicken

__all__ = ['Quicken']
