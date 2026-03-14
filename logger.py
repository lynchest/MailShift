import logging
import sys
from pathlib import Path

def setup_logger(name: str) -> logging.Logger:
    """Create and configure a standard logger."""
    logger = logging.getLogger(name)
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Console handler: keep on stderr so Rich progress (stdout) is less likely to get visually corrupted.
        c_handler = logging.StreamHandler(sys.stderr)
        c_handler.setLevel(logging.WARNING)  # Less verbose by default on console
        
        # File handler
        logs_dir = Path("logs")
        logs_dir.mkdir(exist_ok=True)
        log_file = logs_dir / "mailshift.log"
        f_handler = logging.FileHandler(log_file, encoding="utf-8")
        f_handler.setLevel(logging.INFO)
        
        # Formatters
        c_format = logging.Formatter('%(name)s - %(levelname)s - %(message)s')
        f_format = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
        
        c_handler.setFormatter(c_format)
        f_handler.setFormatter(f_format)
        
        logger.addHandler(c_handler)
        logger.addHandler(f_handler)
        
    return logger

# Default root-like logger for quick usage
log = setup_logger("MailShift")
