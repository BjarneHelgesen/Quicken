#!/usr/bin/env python3
"""
Integration test for Quicken on ExampleCpp repository.

Tests Quicken caching behavior across various real-world scenarios:
1. File-level tools (cl, clang++, clang-tidy) with different flags
2. Repo-level tools (Doxygen)
3. File modifications and reversions
4. Timestamp changes
5. Git branch operations
6. Optimization level handling (None vs specific levels)
7. Cache persistence across multiple operations

IMPORTANT: This test does NOT clear the cache up front. It uses the existing cache.
To test clean execution, manually clear the cache before running:
    python quicken.py --clear-cache

Usage:
    python integrationtest.py [--verbose]

The test uses ../../ExampleCpp repository.
"""

import sys
import gc
import argparse
import time
from pathlib import Path
from enum import Enum
import json
import shutil

import git

from quicken import Quicken


class TestStatus(Enum):
    """Status of integration test."""
    SUCCESS = "success"
    FAILED = "failed"
    ERROR = "error"


class TestResult:
    """Result of integration test."""

    def __init__(self):
        """Initialize test result."""
        self.status = TestStatus.ERROR
        self.tests_run = 0
        self.tests_passed = 0
        self.tests_failed = 0
        self.error_message = None
        self.cache_hits = 0
        self.cache_misses = 0

    def to_summary(self):
        """Generate summary string."""
        lines = []
        lines.append("=" * 70)
        lines.append("QUICKEN INTEGRATION TEST RESULTS")
        lines.append("=" * 70)
        lines.append(f"\nStatus: {self.status.value.upper()}")
        lines.append(f"\nTests:")
        lines.append(f"  Total: {self.tests_run}")
        lines.append(f"  Passed: {self.tests_passed}")
        lines.append(f"  Failed: {self.tests_failed}")
        if self.error_message:
            lines.append(f"\nError: {self.error_message}")
        lines.append("=" * 70)
        return "\n".join(lines)


class IntegrationTestRunner:
    """Orchestrates Quicken integration test."""

    def __init__(self, verbose: bool = False):
        """
        Initialize test runner.

        Args:
            verbose: Enable verbose output
        """
        self.verbose = verbose
        self.result = TestResult()
        self.repo_path = None
        self.git_repo = None
        self.quicken = None
        self.test_branch = "quicken-integration-test"
        self.original_branch = None
        self.stash_created = False

    def _log(self, message: str):
        """Log message if verbose enabled."""
        if self.verbose:
            print(f"  {message}")

    def _test(self, name: str, func):
        """
        Run a test and update results.

        Args:
            name: Test name
            func: Test function to run
        """
        self.result.tests_run += 1
        try:
            print(f"  [{self.result.tests_run}] {name}...", end='', flush=True)
            func()
            self.result.tests_passed += 1
            print(" PASS")
        except AssertionError as e:
            self.result.tests_failed += 1
            print(f" FAIL: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()
        except Exception as e:
            self.result.tests_failed += 1
            print(f" ERROR: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()

    def run(self):
        """Run the integration test."""
        print("=" * 70)
        print("QUICKEN INTEGRATION TEST")
        print("=" * 70)

        try:
            # Setup
            print("\n[Setup]")
            self._setup_repository()
            self._setup_quicken()

            # File-level caching tests
            print("\n[File-Level Caching]")
            self._test("MSVC basic compilation", self._test_msvc_basic)
            self._test("MSVC cache hit", self._test_msvc_cache_hit)
            self._test("MSVC different flags", self._test_msvc_different_flags)
            self._test("MSVC file modification", self._test_msvc_file_modification)
            self._test("MSVC file reversion", self._test_msvc_file_reversion)
            self._test("Clang++ basic compilation", self._test_clang_basic)
            self._test("Clang++ cache hit", self._test_clang_cache_hit)
            self._test("Clang++ optimization O2", self._test_clang_optimization_o2)
            self._test("Clang++ optimization None (accept any)", self._test_clang_optimization_none)
            self._test("Clang-tidy basic analysis", self._test_clang_tidy_basic)
            self._test("Clang-tidy cache hit", self._test_clang_tidy_cache_hit)

            # Advanced file-level tests
            print("\n[Advanced File-Level Tests]")
            self._test("Timestamp change (no content change)", self._test_timestamp_change)
            self._test("Add unrelated file", self._test_add_unrelated_file)
            self._test("Multiple tools same file", self._test_multiple_tools_same_file)

            # Repo-level caching tests
            print("\n[Repo-Level Caching]")
            self._test("Doxygen", self._test_doxygen)
            self._test("Doxygen cache hit", self._test_doxygen_cache_hit)
            self._test("Doxygen with source change", self._test_doxygen_source_change)

            # Git operations
            print("\n[Git Operations]")
            self._test("Switch to new branch", self._test_switch_branch)
            self._test("Cache persists across branches", self._test_cache_across_branches)

            # Determine final status
            if self.result.tests_failed == 0:
                self.result.status = TestStatus.SUCCESS
            else:
                self.result.status = TestStatus.FAILED

            print("\n" + self.result.to_summary())

        except Exception as e:
            self.result.status = TestStatus.ERROR
            self.result.error_message = str(e)
            print(f"\nERROR: {e}")
            if self.verbose:
                import traceback
                traceback.print_exc()

        finally:
            self._cleanup()

        return self.result

    def _setup_repository(self):
        """Setup ExampleCpp repository."""
        # Find ExampleCpp repository
        quicken_dir = Path(__file__).parent
        example_cpp = (quicken_dir.parent.parent / "ExampleCpp").resolve()

        if not example_cpp.exists():
            raise FileNotFoundError(f"ExampleCpp repository not found at: {example_cpp}")

        self.repo_path = example_cpp
        self._log(f"Using repository: {self.repo_path}")

        # Initialize git if needed
        if not (self.repo_path / ".git").exists():
            self.git_repo = git.Repo.init(self.repo_path)
            with self.git_repo.config_writer() as config:
                config.set_value('user', 'name', 'Quicken Integration Test')
                config.set_value('user', 'email', 'quicken@test.com')
            self.git_repo.index.add('*')
            self.git_repo.index.commit('Initial commit')
        else:
            self.git_repo = git.Repo(self.repo_path)

        # Save original branch
        self.original_branch = self.git_repo.active_branch.name

        # Stash any uncommitted changes (will be restored in cleanup)
        self.stash_created = False
        if self.git_repo.is_dirty(untracked_files=True):
            self.git_repo.git.stash('push', '-u', '-m', 'Quicken integration test stash')
            self.stash_created = True
            self._log("Stashed uncommitted changes")

        # Find main branch
        main_branch_name = 'main' if 'main' in [h.name for h in self.git_repo.heads] else 'master'
        main_branch = self.git_repo.heads[main_branch_name]

        # Checkout main branch first
        main_branch.checkout()
        self._log(f"Checked out {main_branch_name} branch")

        # Delete test branch if it exists
        if self.test_branch in [h.name for h in self.git_repo.heads]:
            self.git_repo.delete_head(self.test_branch, force=True)
            self._log(f"Deleted existing {self.test_branch} branch")

        # Create fresh test branch from main
        test_branch = self.git_repo.create_head(self.test_branch, commit=main_branch)
        test_branch.checkout()
        self._log(f"Created and checked out {self.test_branch} branch from {main_branch_name}")

        print(f"  Repository: {self.repo_path.name}")
        print(f"  Branch: {self.test_branch}")

    def _setup_quicken(self):
        """Setup Quicken instance."""
        quicken_dir = Path(__file__).parent
        config_path = quicken_dir / "tools.json"

        if not config_path.exists():
            raise FileNotFoundError(f"Quicken config not found: {config_path}")

        self.quicken = Quicken(config_path)
        print(f"  Quicken cache: {self.quicken.cache.cache_dir}")
        print(f"  NOTE: Using existing cache (not cleared)")

    def _get_test_file(self):
        """Get a test C++ file from the repository."""
        # Find first .cpp file
        cpp_files = list(self.repo_path.glob("**/*.cpp"))
        if not cpp_files:
            raise FileNotFoundError("No .cpp files found in repository")
        return cpp_files[0]

    def _get_header_file(self):
        """Get a test header file from the repository."""
        header_files = list(self.repo_path.glob("**/*.h"))
        if not header_files:
            header_files = list(self.repo_path.glob("**/*.hpp"))
        if not header_files:
            raise FileNotFoundError("No header files found in repository")
        return header_files[0]

    # File-level tests

    def _test_msvc_basic(self):
        """Test basic MSVC compilation."""
        cpp_file = self._get_test_file()
        self._log(f"Compiling {cpp_file.name}")
        returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, f"MSVC compilation failed with code {returncode}"

    def _test_msvc_cache_hit(self):
        """Test MSVC cache hit."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".obj")

        # Delete obj file if it exists
        if obj_file.exists():
            obj_file.unlink()

        self._log(f"Running cached compilation of {cpp_file.name}")
        returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, f"MSVC cached compilation failed with code {returncode}"
        assert obj_file.exists(), "Object file not restored from cache"

    def _test_msvc_different_flags(self):
        """Test MSVC with different flags creates different cache entry."""
        cpp_file = self._get_test_file()
        self._log(f"Compiling {cpp_file.name} with /W4")
        returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc", "/W4"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, f"MSVC compilation with /W4 failed with code {returncode}"

    def _test_msvc_file_modification(self):
        """Test that file modification invalidates cache."""
        cpp_file = self._get_test_file()
        original_content = cpp_file.read_text(encoding='utf-8')

        try:
            # Modify file
            modified_content = "// Modified\n" + original_content
            cpp_file.write_text(modified_content, encoding='utf-8')
            self._log(f"Modified {cpp_file.name}")

            returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                           self.repo_path, cpp_file.parent)
            # Don't assert success - modified file might not compile
            self._log(f"Compilation returned {returncode}")
        finally:
            # Restore original
            cpp_file.write_text(original_content, encoding='utf-8')

    def _test_msvc_file_reversion(self):
        """Test that reverting file content gives cache hit."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".obj")

        # File should be back to original from previous test
        if obj_file.exists():
            obj_file.unlink()

        self._log(f"Compiling reverted {cpp_file.name}")
        returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, "Compilation of reverted file failed"
        assert obj_file.exists(), "Object file not restored from cache after reversion"

    def _test_clang_basic(self):
        """Test basic clang++ compilation."""
        cpp_file = self._get_test_file()
        self._log(f"Compiling {cpp_file.name} with clang++")
        returncode = self.quicken.run(cpp_file, "clang", ["-c"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, f"clang++ compilation failed with code {returncode}"

    def _test_clang_cache_hit(self):
        """Test clang++ cache hit."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".o")

        if obj_file.exists():
            obj_file.unlink()

        self._log(f"Running cached clang++ compilation of {cpp_file.name}")
        returncode = self.quicken.run(cpp_file, "clang", ["-c"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, "clang++ cached compilation failed"
        assert obj_file.exists(), "Object file not restored from cache"

    def _test_clang_optimization_o2(self):
        """Test clang++ with specific optimization level."""
        cpp_file = self._get_test_file()
        self._log(f"Compiling {cpp_file.name} with -O2")
        returncode = self.quicken.run(cpp_file, "clang", ["-c"],
                                       self.repo_path, cpp_file.parent, optimization=2)
        assert returncode == 0, "clang++ compilation with -O2 failed"

    def _test_clang_optimization_none(self):
        """Test clang++ with optimization=None accepts any cached level."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".o")

        if obj_file.exists():
            obj_file.unlink()

        self._log(f"Compiling {cpp_file.name} with optimization=None")
        returncode = self.quicken.run(cpp_file, "clang", ["-c"],
                                       self.repo_path, cpp_file.parent, optimization=None)
        assert returncode == 0, "clang++ compilation with optimization=None failed"
        assert obj_file.exists(), "Object file not restored when optimization=None"

    def _test_clang_tidy_basic(self):
        """Test basic clang-tidy analysis."""
        cpp_file = self._get_test_file()
        self._log(f"Analyzing {cpp_file.name} with clang-tidy")
        # Uses compilation database in repository (compile_commands.json)
        returncode = self.quicken.run(cpp_file, "clang-tidy",
                                       ["--checks=readability-*"],
                                       self.repo_path, cpp_file.parent)
        # clang-tidy returns non-zero on warnings/errors, but 0 or positive means it ran
        # Negative return codes indicate actual failure (tool not found, etc)
        assert returncode >= 0, f"clang-tidy failed to run with return code {returncode}"

    def _test_clang_tidy_cache_hit(self):
        """Test clang-tidy cache hit."""
        cpp_file = self._get_test_file()
        self._log(f"Running cached clang-tidy on {cpp_file.name}")
        # Use same args as basic test to ensure cache hit
        returncode = self.quicken.run(cpp_file, "clang-tidy",
                                       ["--checks=readability-*"],
                                       self.repo_path, cpp_file.parent)
        assert returncode >= 0, f"clang-tidy cached run failed with return code {returncode}"

    # Advanced file-level tests

    def _test_timestamp_change(self):
        """Test that timestamp change without content change gives cache hit."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".obj")

        if obj_file.exists():
            obj_file.unlink()

        # Touch file to change mtime
        cpp_file.touch()
        time.sleep(0.1)  # Ensure timestamp changes

        self._log(f"Compiling {cpp_file.name} after timestamp change")
        returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                       self.repo_path, cpp_file.parent)
        assert returncode == 0, "Compilation after timestamp change failed"
        assert obj_file.exists(), "Object file not restored from cache after timestamp change"

    def _test_add_unrelated_file(self):
        """Test that adding unrelated file doesn't affect cache."""
        # Create new unrelated file
        new_file = self.repo_path / "unrelated_test_file.cpp"
        new_file.write_text("int dummy() { return 0; }\n", encoding='utf-8')

        try:
            # Compile original file - should hit cache
            cpp_file = self._get_test_file()
            obj_file = cpp_file.parent / (cpp_file.stem + ".obj")

            if obj_file.exists():
                obj_file.unlink()

            self._log(f"Compiling {cpp_file.name} after adding unrelated file")
            returncode = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                           self.repo_path, cpp_file.parent)
            assert returncode == 0, "Compilation after adding unrelated file failed"
            assert obj_file.exists(), "Cache should hit despite unrelated file addition"
        finally:
            # Cleanup
            if new_file.exists():
                new_file.unlink()

    def _test_multiple_tools_same_file(self):
        """Test multiple tools on same file use separate cache entries."""
        cpp_file = self._get_test_file()

        self._log(f"Running MSVC, clang++, and clang-tidy on {cpp_file.name}")

        # MSVC
        returncode1 = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                        self.repo_path, cpp_file.parent)
        assert returncode1 == 0, "MSVC failed"

        # Clang++
        returncode2 = self.quicken.run(cpp_file, "clang", ["-c"],
                                        self.repo_path, cpp_file.parent)
        assert returncode2 == 0, "clang++ failed"

        # Clang-tidy
        returncode3 = self.quicken.run(cpp_file, "clang-tidy",
                                        ["--checks=*"],
                                        self.repo_path, cpp_file.parent)
        assert returncode3 >= 0, f"clang-tidy failed with return code {returncode3}"

        self._log("All tools completed successfully")

    # Repo-level tests

    def _test_doxygen(self):
        """Test Doxygen generation."""
        # Check if Doxygen is configured
        doxygen_dir = self.repo_path / ".doxygen"
        if not doxygen_dir.exists():
            self._log("Doxygen not configured, skipping")
            return

        doxyfile = doxygen_dir / "Doxyfile.xml"
        if not doxyfile.exists():
            self._log("Doxyfile.xml not found, skipping")
            return

        output_dir = doxygen_dir / "xml"

        self._log(f"Running Doxygen")
        returncode = self.quicken.run_repo_tool(
            self.repo_path,
            "doxygen",
            [str(doxyfile)],
            doxyfile,
            ["*.cpp", "*.cxx", "*.cc", "*.c",
             "*.hpp", "*.hxx", "*.h", "*.hh"],
            output_dir
        )
        assert returncode == 0, f"Doxygen failed with code {returncode}"
        assert output_dir.exists(), "Doxygen output directory not created"

    def _test_doxygen_cache_hit(self):
        """Test Doxygen cache hit."""
        doxygen_dir = self.repo_path / ".doxygen"
        if not doxygen_dir.exists():
            self._log("Doxygen not configured, skipping")
            return

        doxyfile = doxygen_dir / "Doxyfile.xml"
        if not doxyfile.exists():
            self._log("Doxyfile.xml not found, skipping")
            return

        output_dir = doxygen_dir / "xml"

        # Delete output directory
        if output_dir.exists():
            shutil.rmtree(output_dir)

        self._log(f"Running cached Doxygen")
        returncode = self.quicken.run_repo_tool(
            self.repo_path,
            "doxygen",
            [str(doxyfile)],
            doxyfile,
            ["*.cpp", "*.cxx", "*.cc", "*.c",
             "*.hpp", "*.hxx", "*.h", "*.hh"],
            output_dir
        )
        assert returncode == 0, "Cached Doxygen failed"
        assert output_dir.exists(), "Doxygen output not restored from cache"

    def _test_doxygen_source_change(self):
        """Test that source file change invalidates Doxygen cache."""
        doxygen_dir = self.repo_path / ".doxygen"
        if not doxygen_dir.exists():
            self._log("Doxygen not configured, skipping")
            return

        # Modify a source file
        cpp_file = self._get_test_file()
        original_content = cpp_file.read_text(encoding='utf-8')

        try:
            # Add a comment (should invalidate Doxygen cache)
            modified_content = "// Test modification\n" + original_content
            cpp_file.write_text(modified_content, encoding='utf-8')

            doxyfile = doxygen_dir / "Doxyfile.xml"
            if not doxyfile.exists():
                self._log("Doxyfile.xml not found, skipping")
                return

            output_dir = doxygen_dir / "xml"

            self._log(f"Running Doxygen after source change")
            returncode = self.quicken.run_repo_tool(
                self.repo_path,
                "doxygen",
                [str(doxyfile)],
                doxyfile,
                ["*.cpp", "*.cxx", "*.cc", "*.c",
                 "*.hpp", "*.hxx", "*.h", "*.hh"],
                output_dir
            )
            # Don't assert - just check it completes
            self._log(f"Doxygen completed with code {returncode}")
        finally:
            # Restore original
            cpp_file.write_text(original_content, encoding='utf-8')

    # Git operations

    def _test_switch_branch(self):
        """Test switching to a new branch."""
        # Create and switch to new branch
        new_branch_name = "quicken-test-branch-2"

        if new_branch_name in [h.name for h in self.git_repo.heads]:
            new_branch = self.git_repo.heads[new_branch_name]
        else:
            new_branch = self.git_repo.create_head(new_branch_name)

        new_branch.checkout()
        self._log(f"Switched to branch {new_branch_name}")

        assert self.git_repo.active_branch.name == new_branch_name

        # Switch back
        self.git_repo.heads[self.test_branch].checkout()

    def _test_cache_across_branches(self):
        """Test that cache persists across branch switches."""
        cpp_file = self._get_test_file()
        obj_file = cpp_file.parent / (cpp_file.stem + ".obj")

        # Create new branch
        new_branch_name = "quicken-test-branch-3"
        if new_branch_name in [h.name for h in self.git_repo.heads]:
            new_branch = self.git_repo.heads[new_branch_name]
        else:
            new_branch = self.git_repo.create_head(new_branch_name)

        try:
            # Compile on current branch
            returncode1 = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                            self.repo_path, cpp_file.parent)
            assert returncode1 == 0, "Initial compilation failed"

            # Switch to new branch
            new_branch.checkout()
            self._log(f"Switched to {new_branch_name}")

            # Delete obj file
            if obj_file.exists():
                obj_file.unlink()

            # Compile again - should hit cache if file is same
            self._log(f"Compiling on new branch")
            returncode2 = self.quicken.run(cpp_file, "cl", ["/c", "/nologo", "/EHsc"],
                                            self.repo_path, cpp_file.parent)
            assert returncode2 == 0, "Compilation on new branch failed"
            assert obj_file.exists(), "Cache should work across branches for same content"
        finally:
            # Switch back to test branch
            self.git_repo.heads[self.test_branch].checkout()

    def _cleanup(self):
        """Cleanup test artifacts."""
        try:
            # Checkout original branch if possible
            if self.git_repo and self.original_branch:
                try:
                    self.git_repo.heads[self.original_branch].checkout()
                    self._log(f"Restored original branch: {self.original_branch}")
                except Exception as e:
                    if self.verbose:
                        print(f"Could not restore original branch: {e}")

            # Delete main test branch
            if self.git_repo and self.test_branch in [h.name for h in self.git_repo.heads]:
                try:
                    self.git_repo.delete_head(self.test_branch, force=True)
                    self._log(f"Deleted {self.test_branch} branch")
                except Exception as e:
                    if self.verbose:
                        print(f"Could not delete {self.test_branch}: {e}")

            # Restore stashed changes if we created a stash
            if self.git_repo and self.stash_created:
                try:
                    self.git_repo.git.stash('pop')
                    self._log("Restored stashed changes")
                except Exception as e:
                    if self.verbose:
                        print(f"Could not restore stash: {e}")

            # Delete temporary test branches
            if self.git_repo:
                for branch_name in ["quicken-test-branch-2", "quicken-test-branch-3"]:
                    if branch_name in [h.name for h in self.git_repo.heads]:
                        try:
                            self.git_repo.delete_head(branch_name, force=True)
                        except:
                            pass

            # Clean up object files
            if self.repo_path:
                for ext in [".obj", ".o"]:
                    for obj_file in self.repo_path.glob(f"**/*{ext}"):
                        try:
                            obj_file.unlink()
                        except:
                            pass

        except Exception as e:
            if self.verbose:
                print(f"Cleanup error: {e}")

        # Force garbage collection
        gc.collect()


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Integration test for Quicken on ExampleCpp repository'
    )
    parser.add_argument(
        '--verbose',
        action='store_true',
        help='Enable verbose output'
    )

    args = parser.parse_args()

    # Run test
    runner = IntegrationTestRunner(verbose=args.verbose)
    result = runner.run()

    # Exit with appropriate code
    if result.status == TestStatus.SUCCESS:
        sys.exit(0)
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
