#!/usr/bin/env python3
"""
Tests for QuickenCL argument parsing
"""

import pytest
from pathlib import Path
from QuickenCL import parse_cl_arguments


def test_parse_basic_compilation():
    """Test basic compilation: cl /c file.cpp"""
    args = ["/c", "test.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert source_files[0] == Path("test.cpp")
    assert "/c" in compiler_args
    assert output_dir is None


def test_parse_multiple_files():
    """Test multiple files: cl /c file1.cpp file2.cpp"""
    args = ["/c", "file1.cpp", "file2.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 2
    assert source_files[0] == Path("file1.cpp")
    assert source_files[1] == Path("file2.cpp")
    assert "/c" in compiler_args


def test_parse_output_directory():
    """Test output directory: cl /c /Foobj/ file.cpp"""
    args = ["/c", "/Foobj/", "file.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert source_files[0] == Path("file.cpp")
    assert output_dir == Path("obj")


def test_parse_output_directory_backslash():
    """Test output directory with backslash: cl /c /Foobj\\ file.cpp"""
    args = ["/c", "/Foobj\\", "file.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert output_dir == Path("obj")


def test_parse_output_file():
    """Test output file: cl /c /Fooutput/file.obj file.cpp"""
    args = ["/c", "/Fooutput/file.obj", "file.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert output_dir == Path("output")


def test_parse_complex_arguments():
    """Test complex arguments: cl /c /W4 /O2 /EHsc file.cpp"""
    args = ["/c", "/W4", "/O2", "/EHsc", "file.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert "/W4" in compiler_args
    assert "/O2" in compiler_args
    assert "/EHsc" in compiler_args


def test_parse_file_at_beginning():
    """Test file at beginning: cl file.cpp /c /W4"""
    args = ["file.cpp", "/c", "/W4"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert source_files[0] == Path("file.cpp")


def test_parse_different_extensions():
    """Test different C/C++ extensions"""
    test_cases = [
        (["test.cpp"], ".cpp"),
        (["test.cxx"], ".cxx"),
        (["test.cc"], ".cc"),
        (["test.c"], ".c"),
        (["test.c++"], ".c++"),
    ]

    for args, expected_ext in test_cases:
        source_files, _, _ = parse_cl_arguments(args)
        assert len(source_files) == 1
        assert source_files[0].suffix.lower() == expected_ext


def test_parse_quoted_output_directory():
    """Test quoted output directory: cl /c /Fo"output dir/" file.cpp"""
    args = ["/c", '/Fo"output dir/"', "file.cpp"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 1
    assert output_dir == Path("output dir")


def test_parse_no_source_files():
    """Test no source files: cl /help"""
    args = ["/help"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 0
    assert "/help" in compiler_args


def test_parse_mixed_order():
    """Test mixed order: cl /W4 file1.cpp /O2 file2.cpp /c"""
    args = ["/W4", "file1.cpp", "/O2", "file2.cpp", "/c"]
    source_files, compiler_args, output_dir = parse_cl_arguments(args)

    assert len(source_files) == 2
    assert source_files[0] == Path("file1.cpp")
    assert source_files[1] == Path("file2.cpp")
    assert "/W4" in compiler_args
    assert "/O2" in compiler_args
    assert "/c" in compiler_args


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
