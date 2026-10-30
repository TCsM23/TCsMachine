"""Bump the version and open a CHANGELOG entry.   python packaging/bump.py 0.3.0 "what changed"
Then commit and push to main: CI builds the installers and publishes the release."""
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
new, note = sys.argv[1], " ".join(sys.argv[2:]) or "Describe the changes here."
if not re.fullmatch(r"\d+\.\d+\.\d+", new):
    sys.exit("version must look like 1.2.3")
init = ROOT / "pale" / "__init__.py"
init.write_text(re.sub(r'__version__ = "[^"]+"', f'__version__ = "{new}"', init.read_text()))
cl = ROOT / "CHANGELOG.md"
text = cl.read_text(encoding="utf-8")
entry = f"## {new} · {date.today()}\n\n- {note}\n\n"
cl.write_text(text.replace("\n## ", "\n" + entry + "## ", 1) if "\n## " in text else text + "\n" + entry, encoding="utf-8")
print(f"bumped to {new}. Commit, push to main, and the release workflow does the rest.")
