"""Example pre_tool hook: block any write whose content looks like it contains a secret.

pale.toml:
    [hooks]
    pre_tool = ["python examples/hooks/no_secrets_in_writes.py"]

The hook gets the event as JSON on stdin. Exit 2 blocks the call; stderr goes back to the unit.
"""
import json
import re
import sys

event = json.load(sys.stdin)
if event.get("tool") in ("write_file", "edit_file"):
    text = json.dumps(event.get("input", {}))
    if re.search(r"sk-ant-|ghp_|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY", text):
        sys.stderr.write("that content looks like it contains a credential; write a placeholder instead")
        sys.exit(2)
sys.exit(0)
