#!/usr/bin/env python3
"""
QuickenCL - Drop-in replacement for cl.exe with caching

This script takes the exact same parameters as cl.exe (MSVC compiler) and calls
Quicken to either return results from cache or forward the call to cl.exe.

Can be compiled to an .exe to act as a cl.exe replacement in build systems.

Usage:
    python QuickenCL.py [cl.exe arguments]
    QuickenCL.exe [cl.exe arguments]

Examples:
    QuickenCL.py /c /W4 myfile.cpp
    QuickenCL.py /c /Foobj/ file1.cpp file2.cpp
    QuickenCL.py myfile.cpp /c /O2
"""

import sys
from pathlib import Path
from quicken import Quicken


def parse_cl_arguments(args):
    """
    Parse cl.exe command-line arguments to extract:
    - Source files (.cpp, .c, .cxx, .cc files)
    - Output directory (from /Fo flag)
    - All other compiler arguments

    Args:
        args: List of command-line arguments (excluding program name)

    Returns:
        Tuple of (source_files, compiler_args, output_dir)
    """
    source_files = []
    compiler_args = []
    output_dir = None

    # Common C++ source file extensions
    source_extensions = {'.cpp', '.cxx', '.cc', '.c', '.c++'}

    i = 0
    while i < len(args):
        arg = args[i]

        # Check if this is a source file
        arg_path = Path(arg)
        if arg_path.suffix.lower() in source_extensions:
            source_files.append(arg_path)
            i += 1
            continue

        # Check for /Fo flag (output directory or file)
        if arg.startswith('/Fo') or arg.startswith('-Fo'):
            # Extract path after /Fo
            fo_path = arg[3:]  # Remove /Fo prefix

            # Remove quotes if present
            if fo_path.startswith('"') and fo_path.endswith('"'):
                fo_path = fo_path[1:-1]

            # Check if it's a directory (ends with / or \) or a file
            if fo_path and (fo_path.endswith('/') or fo_path.endswith('\\')):
                # It's a directory
                output_dir = Path(fo_path.rstrip('/\\'))
            elif fo_path:
                # It's a file path, extract the directory
                output_dir = Path(fo_path).parent
                if not str(output_dir) or str(output_dir) == '.':
                    output_dir = None

        # Add to compiler arguments
        compiler_args.append(arg)
        i += 1

    return source_files, compiler_args, output_dir


def main():
    """Main entry point for QuickenCL."""
    # Get command-line arguments (excluding program name)
    args = sys.argv[1:]

    # Handle empty arguments
    if not args:
        print("QuickenCL - cl.exe replacement with caching", file=sys.stderr)
        print("Usage: QuickenCL [cl.exe arguments]", file=sys.stderr)
        sys.exit(1)

    # Parse cl.exe arguments
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    # If no source files found, pass through to cl.exe
    # This handles cases like "cl /help" or link-only operations
    if not source_files:
        # Just forward to quicken with the original arguments
        # Let quicken.py handle the error
        print("QuickenCL: No source files found in arguments", file=sys.stderr)
        sys.exit(1)

    # Determine configuration file path
    # Look for tools.json in the same directory as this script
    script_dir = Path(__file__).parent
    config_path = script_dir / "tools.json"

    # If not found, try current working directory
    if not config_path.exists():
        config_path = Path.cwd() / "tools.json"

    if not config_path.exists():
        print(f"QuickenCL: Error: tools.json not found", file=sys.stderr)
        print(f"  Looked in: {script_dir} and {Path.cwd()}", file=sys.stderr)
        sys.exit(1)

    try:
        # Initialize Quicken once
        quicken = Quicken(config_path)

        # Determine repository directory (current working directory)
        repo_dir = Path.cwd()

        # Track overall success
        overall_returncode = 0

        # Process each source file
        for source_file in source_files:
            # Resolve source file path
            source_path = source_file.resolve()

            # Determine output directory
            # Priority: /Fo flag > source file directory
            if output_dir:
                output_directory = output_dir.resolve()
            else:
                output_directory = source_path.parent

            # Run through Quicken
            returncode = quicken.run(
                source_file=source_path,
                tool_name="cl",
                tool_args=compiler_args,
                repo_dir=repo_dir,
                output_dir=output_directory
            )

            # Track failures
            if returncode != 0:
                overall_returncode = returncode

        # Return the exit code
        sys.exit(overall_returncode)

    except Exception as e:
        print(f"QuickenCL: Error: {e}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
