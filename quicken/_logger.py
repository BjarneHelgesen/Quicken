"""Logging functionality for Quicken operations."""

import logging
from pathlib import Path

from ._type_check import typecheck_methods


@typecheck_methods
class QuickenLogger(logging.Logger):
    """Logger for Quicken operations."""

    def __init__(self, log_dir: Path):
        """Initialize logger with file handler.
        Args:    log_dir: Directory where log file will be created"""
        super().__init__("Quicken", logging.INFO)

        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "quicken.log"

        # Remove existing handlers to avoid duplicates
        self.handlers.clear()

        # File handler
        handler = logging.FileHandler(log_file)
        handler.setLevel(logging.INFO)

        # Format: timestamp - level - message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
        handler.setFormatter(formatter)

        self.addHandler(handler)
