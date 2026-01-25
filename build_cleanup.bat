@echo off
REM Build cleanup.py into a standalone executable using Nuitka
REM
REM Requirements:
REM   - Python 3.8+
REM   - pip install nuitka
REM   - quicken package installed (pip install -e . from repo root)
REM   - C compiler (MSVC or MinGW)

nuitka --onefile --include-package=quicken --output-filename=quicken-cleanup.exe cleanup.py
