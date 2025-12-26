#!/usr/bin/env python3
"""
Performance and coverage test for two specific cl.exe configurations.
"""

import json
import tempfile
from pathlib import Path

import pytest

from quicken import Quicken


# Simple C++ code for testing
TEST_CPP_CODE = """
#include <iostream>

int main() {
    std::cout << "Hello, World!" << std::endl;
    return 0;
}
"""


@pytest.fixture
def temp_dir():
    """Create a temporary directory for test files."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def test_cpp_file(temp_dir):
    """Create a test.cpp file."""
    cpp_file = temp_dir / "test.cpp"
    cpp_file.write_text(TEST_CPP_CODE)
    return cpp_file


@pytest.fixture
def config_file(temp_dir):
    """Create a test config file pointing to the real tools.json."""
    # Use the actual tools.json from the project
    project_tools = Path(__file__).parent / "tools.json"
    if project_tools.exists():
        return project_tools

    # Fallback: create a minimal config
    config = temp_dir / "tools.json"
    config_data = {
        "cl": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Tools\\MSVC\\14.44.35207\\bin\\Hostx64\\x64\\cl.exe",
        "vcvarsall": "C:\\Program Files\\Microsoft Visual Studio\\2022\\Community\\VC\\Auxiliary\\Build\\vcvarsall.bat",
        "msvc_arch": "x64"
    }
    config.write_text(json.dumps(config_data, indent=2))
    return config


@pytest.fixture
def quicken_instance(config_file):
    """Create a Quicken instance."""
    return Quicken(config_file)


class TestCacheHits:
    """Performance and coverage test for specific argument configurations."""

    def test_two_configurations(self, quicken_instance, test_cpp_file):
        """
        Test two specific cl.exe configurations:
        1. ['/Od', '/c', '/nologo', '/EHsc']
        2. ['/Od', '/std:c++20', '/Zc:strictStrings-', '/EHsc', '/nologo', '/W3', '/c']
        """
        # Configuration 1: Basic optimization disabled
        args_config1 = ['/Od', '/c', '/nologo', '/EHsc']

        # Configuration 2: C++20 with additional flags
        args_config2 = [
            '/Od',
            '/std:c++20',
            '/Zc:strictStrings-',
            '/EHsc',
            '/nologo',
            '/W3',
            '/c'
        ]

        # Run both configurations
        returncode1 = quicken_instance.run(
            test_cpp_file,
            "cl",
            args_config1,
            repo_dir=test_cpp_file.parent,
            output_dir=test_cpp_file.parent
        )
        assert returncode1 == 0

        returncode2 = quicken_instance.run(
            test_cpp_file,
            "cl",
            args_config2,
            repo_dir=test_cpp_file.parent,
            output_dir=test_cpp_file.parent
        )
        assert returncode2 == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
