"""
Type checking decorator for Quicken.

Provides runtime type checking that is only active during unit tests.
"""

import functools
import inspect
from pathlib import Path
from typing import get_type_hints, get_origin, get_args, List, Optional, Union


# Set to True by Quicken's test fixtures to enable type checking
TYPECHECK_ENABLED = False


def _check_type(value, expected_type, param_name: str):
    """Check if value matches expected type, raise TypeError if not."""
    # Handle None for Optional types
    if value is None:
        origin = get_origin(expected_type)
        if origin is Union:
            args = get_args(expected_type)
            if type(None) in args:
                return  # None is valid for Optional
        if expected_type is type(None):
            return
        raise TypeError(f"Parameter '{param_name}' expected {expected_type}, got None")

    # Get the origin type for generic types (e.g., List[str] -> list)
    origin = get_origin(expected_type)

    if origin is None:
        # Simple type like str, int, Path
        if expected_type is Path:
            # Accept Path or str that can be converted to Path
            if not isinstance(value, (Path, str)):
                raise TypeError(f"Parameter '{param_name}' expected Path, got {type(value).__name__}")
        elif not isinstance(value, expected_type):
            raise TypeError(f"Parameter '{param_name}' expected {expected_type.__name__}, got {type(value).__name__}")

    elif origin is list:
        # List[X] - check it's a list and optionally check element types
        if not isinstance(value, list):
            raise TypeError(f"Parameter '{param_name}' expected list, got {type(value).__name__}")
        # Check element types if specified
        args = get_args(expected_type)
        if args and value:
            elem_type = args[0]
            for i, elem in enumerate(value):
                if not isinstance(elem, elem_type):
                    raise TypeError(
                        f"Parameter '{param_name}[{i}]' expected {elem_type.__name__}, "
                        f"got {type(elem).__name__}"
                    )

    elif origin is Union:
        # Optional[X] or Union[X, Y, ...]
        args = get_args(expected_type)
        for arg in args:
            if arg is type(None):
                continue
            try:
                _check_type(value, arg, param_name)
                return  # Matched one of the union types
            except TypeError:
                continue
        # None of the types matched
        type_names = [getattr(a, '__name__', str(a)) for a in args if a is not type(None)]
        raise TypeError(
            f"Parameter '{param_name}' expected one of {type_names}, got {type(value).__name__}"
        )


def typecheck(func):
    """Decorator that checks function argument types against type hints.

    Only active when running under pytest. Otherwise returns the function unchanged.
    """
    if not TYPECHECK_ENABLED:
        return func

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        # Get type hints for the function
        try:
            hints = get_type_hints(func)
        except Exception:
            # If we can't get hints, just call the function
            return func(*args, **kwargs)

        # Get function signature to map positional args to parameter names
        sig = inspect.signature(func)
        params = list(sig.parameters.keys())

        # Check positional arguments
        for i, (param_name, value) in enumerate(zip(params, args)):
            if param_name in hints:
                _check_type(value, hints[param_name], param_name)

        # Check keyword arguments
        for param_name, value in kwargs.items():
            if param_name in hints:
                _check_type(value, hints[param_name], param_name)

        return func(*args, **kwargs)

    return wrapper


def typecheck_methods(cls):
    """Class decorator that applies typecheck to all public methods."""
    if not TYPECHECK_ENABLED:
        return cls

    for name, method in inspect.getmembers(cls, predicate=inspect.isfunction):
        # Skip private/magic methods except __init__
        if name.startswith('_') and name != '__init__':
            continue
        setattr(cls, name, typecheck(method))

    return cls
