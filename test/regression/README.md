# Regression Tests

This directory contains regression tests for Quicken - tests that verify previously fixed bugs remain fixed.

## Purpose

Regression tests serve as:
1. **Bug Documentation** - Each test documents a specific bug that was found and fixed
2. **Protection** - Prevents the bug from being reintroduced in future changes
3. **Examples** - Shows users how to properly use the Quicken API

## Structure

Each regression test file should:
- Document the bug clearly in the docstring
- Reference the commit that fixed it
- Use `@pytest.mark.regression_test` decorator
- Verify the fix works correctly using proper assertions
- Be runnable independently

## Running Regression Tests

Run only regression tests:
```bash
pytest -m regression_test
```

Run all tests including regression tests:
```bash
pytest
```

Run all tests EXCEPT regression tests:
```bash
pytest -m "not regression_test"
```

Run a specific regression test:
```bash
pytest test/regression/test_cache_entry_reuse_regression.py -v
```

## Example

See `test_cache_entry_reuse_regression.py` for a complete example of a regression test for commit 4a5ba0f.

## Creating New Regression Tests

When a bug is found:

1. **Create a failing test** - Write a test that demonstrates the bug (it will fail)
2. **Fix the bug** - Update the code to fix the issue
3. **Verify** - The test should now pass
4. **Commit together** - Commit both the fix and the regression test

Each test should:
- Have a clear, descriptive name
- Document what bug it's testing
- Reference the commit that fixed it (if applicable)
- Be marked with `@pytest.mark.regression_test`
- Use proper pytest assertions (not print statements)

## Guidelines

**If API is used incorrectly:**
- Document proper usage
- Add error handling
- Update or delete the regression test accordingly

**If API is used correctly:**
- Fix the bug
- Keep the regression test
- Commit both together
