from pathlib import Path

# The project root is two levels up from this file (src/mailshift/utils/paths.py)
ROOT_DIR = Path(__file__).parent.parent.parent.parent.absolute()

def get_path(relative_path: str) -> Path:
    """Get absolute path relative to the project root."""
    return ROOT_DIR / relative_path
