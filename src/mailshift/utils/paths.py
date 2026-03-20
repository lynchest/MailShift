import os
from pathlib import Path
import platform


def get_path(relative_path: str) -> Path:
    """Get absolute path relative to user's config directory"""
    system = platform.system()

    if system == "Windows":
        base_dir = os.environ.get("APPDATA", os.path.expanduser("~"))
        base_path = Path(base_dir) / "MailShift"
    elif system == "Darwin":
        base_path = Path.home() / "Library" / "Application Support" / "MailShift"
    else:  # Linux and others
        base_path = Path.home() / ".config" / "MailShift"

    return base_path / relative_path
