#!/usr/bin/env python3

import json
import time
from pathlib import Path

import pytest
from quicken import Quicken


# Simple C++ code for testing
TEST_CPP_V1 = """
#include <iostream>

int main() {
    std::cout << "Version 1" << std::endl;
    return 0;
}
"""


@pytest.mark.regression_test
def test_stale_mtime(config_file, temp_dir):
    """
        Bug description: After a file is changed and reverted, all original cached operations get slower.

	Category: Performance issue. 
	Steps to reproduce: 
	1. Quicken.run is called on a new file. 
	2. The file is modified, and Quicken.run is called with the same parameters . 
	3. The the file is reverted, and Quicken.run is called with the same parameters .
        4. Quicken.run is called again with the same parameters . 
	Expected behavour in step 4: Cache HIT based on mtime (fastest cache hit). 	
	Actual behaviour in step 4: Cache HIT based on hashing (slower) 

	Note: the regression test is more compact than the test steps as it just touches the file instead of changing and reverting. 
        """
    quicken = Quicken(config_file)
    quicken.clear_cache()

    test_cpp = temp_dir / "test.cpp"
    cache_dir = Path.home() / ".quicken" / "cache"
    args = ['/c', '/nologo', '/EHsc']

    # Compile V1
    test_cpp.write_text(TEST_CPP_V1)
    returncode = quicken.run(test_cpp, "cl", args, repo_dir=temp_dir)
    assert returncode == 0

    # Get original mtime from metadata
    entry_001 = cache_dir / "entry_000001"
    metadata_file = entry_001 / "metadata.json"
    with open(metadata_file, 'r') as f:
        metadata_v1 = json.load(f)
    original_mtime = metadata_v1["dependencies"][0]["mtime_ns"]

    # Touch file (same content, new mtime)
    time.sleep(0.01)
    test_cpp.write_text(TEST_CPP_V1)

    # Compile again - should be cache hit with mtime update
    returncode = quicken.run(test_cpp, "cl", args, repo_dir=temp_dir)
    assert returncode == 0

    # Verify mtime was updated in metadata
    with open(metadata_file, 'r') as f:
        metadata_v2 = json.load(f)
    new_mtime = metadata_v2["dependencies"][0]["mtime_ns"]

    assert new_mtime != original_mtime, \
        "BUG: mtime should be updated in metadata after cache hit with changed mtime"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-m", "regression_test"])
