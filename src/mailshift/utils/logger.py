import logging

import sys

from pathlib import Path

from .paths import get_path



def setup_logger(name: str) -> logging.Logger:

    """Create and configure a standard logger."""

    logger = logging.getLogger(name)

    

    if not logger.handlers:

        logger.setLevel(logging.INFO)

        

        # Console handler: keep on stderr so Rich progress (stdout) is less likely to get visually corrupted.

        c_handler = logging.StreamHandler(sys.stderr)

        c_handler.setLevel(logging.WARNING)  # Less verbose by default on console

        

        # File handler

        # If path resolution is mocked/broken (e.g., isolated tests), keep logging
        # functional in console-only mode instead of raising at import time.
        f_handler = None
        try:
            logs_dir = get_path("logs")
            logs_dir.mkdir(exist_ok=True)
            log_file = logs_dir / "mailshift.log"
            f_handler = logging.FileHandler(log_file, encoding="utf-8")
            f_handler.setLevel(logging.INFO)
        except Exception:
            f_handler = None

        

        # Formatters

        c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')

        f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')

        

        c_handler.setFormatter(c_format)

        if f_handler:
            f_handler.setFormatter(f_format)

        

        logger.addHandler(c_handler)

        if f_handler:
            logger.addHandler(f_handler)

        

    return logger



# Default root-like logger for quick usage

log = setup_logger("MailShift")

