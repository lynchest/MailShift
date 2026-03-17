import sys
from pathlib import Path

# Add 'src' to sys.path so tests can import 'mailshift'
root_dir = Path(__file__).parent.parent.absolute()
src_path = str(root_dir / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)
