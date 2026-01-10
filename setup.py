from setuptools import setup, find_packages

setup(
    name="quicken",
    version="1.0.0",
    description="Caching wrapper for C++ build tools",
    py_modules=["quicken", "cpp_normalizer"],
    python_requires=">=3.7",
    install_requires=[],
)
