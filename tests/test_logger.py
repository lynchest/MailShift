import logging
import sys
import unittest.mock
from pathlib import Path

import pytest

from mailshift.utils.logger import setup_logger

def test_setup_logger_basic(tmp_path: Path):
    """Test basic logger setup with both console and file handlers."""
    logger_name = "test_logger_basic"

    with unittest.mock.patch("mailshift.utils.logger.get_path") as mock_get_path:
        mock_get_path.return_value = tmp_path

        logger = setup_logger(logger_name)

        assert logger.name == logger_name
        assert logger.level == logging.INFO

        # Should have 2 handlers: console and file
        assert len(logger.handlers) == 2

        # Check StreamHandler (console)
        stream_handlers = [h for h in logger.handlers if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler)]
        assert len(stream_handlers) == 1
        c_handler = stream_handlers[0]
        assert c_handler.level == logging.WARNING
        assert c_handler.stream == sys.stderr

        # Check FileHandler
        file_handlers = [h for h in logger.handlers if isinstance(h, logging.FileHandler)]
        assert len(file_handlers) == 1
        f_handler = file_handlers[0]
        assert f_handler.level == logging.INFO
        assert f_handler.encoding == "utf-8"

        expected_log_file = tmp_path / "mailshift.log"
        assert Path(f_handler.baseFilename) == expected_log_file

def test_setup_logger_fallback():
    """Test logger setup when get_path raises an exception (fallback to console only)."""
    logger_name = "test_logger_fallback"

    with unittest.mock.patch("mailshift.utils.logger.get_path", side_effect=Exception("Path resolution failed")):
        logger = setup_logger(logger_name)

        assert logger.name == logger_name
        assert logger.level == logging.INFO

        # Should have 1 handler: console only
        assert len(logger.handlers) == 1

        handler = logger.handlers[0]
        assert isinstance(handler, logging.StreamHandler)
        assert not isinstance(handler, logging.FileHandler)
        assert handler.level == logging.WARNING
        assert handler.stream == sys.stderr

def test_setup_logger_no_duplicates(tmp_path: Path):
    """Test that calling setup_logger twice doesn't duplicate handlers."""
    logger_name = "test_logger_no_duplicates"

    with unittest.mock.patch("mailshift.utils.logger.get_path") as mock_get_path:
        mock_get_path.return_value = tmp_path

        # First call
        logger1 = setup_logger(logger_name)
        assert len(logger1.handlers) == 2

        # Second call
        logger2 = setup_logger(logger_name)

        # Should be the exact same logger object
        assert logger1 is logger2

        # Still 2 handlers
        assert len(logger2.handlers) == 2
