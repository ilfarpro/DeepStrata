"""Make the checkout root and its tools folder importable for tests that live in a subfolder."""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for folder in (ROOT, ROOT / "tools"):
    if str(folder) not in sys.path:
        sys.path.insert(0, str(folder))
