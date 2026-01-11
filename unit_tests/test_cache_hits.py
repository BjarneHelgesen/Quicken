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
def test_cpp_file(temp_dir):
    """Create a test.cpp file."""
    cpp_file = temp_dir / "test.cpp"
    cpp_file.write_text(TEST_CPP_CODE)
    return cpp_file


@pytest.fixture
def quicken_instance(temp_dir):
    """Create a Quicken instance."""
    return Quicken(temp_dir)


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
            args_config1
        )
        assert returncode1 == 0

        returncode2 = quicken_instance.run(
            test_cpp_file,
            "cl",
            args_config2
        )
        assert returncode2 == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
