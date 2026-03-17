import sys
from pathlib import Path

# Add 'src' to sys.path so we can import 'mailshift'
src_path = str(Path(__file__).parent.absolute() / "src")
if src_path not in sys.path:
    sys.path.insert(0, src_path)

from mailshift.main import main

if __name__ == "__main__":
    main()