"""Print the CHANGELOG.md section for a version (used as the GitHub release body, which the
in-app update dialog shows)."""
import re
import sys
from pathlib import Path

version = sys.argv[1]
text = Path(__file__).resolve().parent.parent.joinpath("CHANGELOG.md").read_text(encoding="utf-8")
m = re.search(rf"^## \[?v?{re.escape(version)}\]?.*?$(.*?)(?=^## |\Z)", text, re.M | re.S)
print(m.group(1).strip() if m else f"PALE v{version}")
