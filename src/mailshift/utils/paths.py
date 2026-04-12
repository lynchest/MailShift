from pathlib import Path

# The project root is two levels up from this file (src/mailshift/utils/paths.py)
# We use .resolve() to ensure ROOT_DIR is the canonical path, which is
# necessary for correct comparison via .is_relative_to() in systems with symlinks.
ROOT_DIR = Path(__file__).parent.parent.parent.parent.resolve()

def get_path(relative_path: str) -> Path:
    """Get absolute path relative to the project root and prevent path traversal."""
    target_path = (ROOT_DIR / relative_path).resolve()

    if not target_path.is_relative_to(ROOT_DIR):
        raise ValueError(f"Path traversal attempt detected: {relative_path}")

    return target_path
