"""Secret scrubbing for tool output, before it reaches the model (and the provider's logs).

If the unit `cat`s a .env file, the model sees `[REDACTED:anthropic_key]`, not the key.
"""

from __future__ import annotations

import os
import re

PATTERNS = [
    ("anthropic_key", r"sk-ant-[A-Za-z0-9_\-]{20,}"),
    ("openai_key", r"sk-(?:proj-)?[A-Za-z0-9]{20,}"),
    ("github_token", r"gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{40,}"),
    ("aws_key", r"\bAKIA[0-9A-Z]{16}\b"),
    ("slack_token", r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    ("telegram_token", r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),
    ("google_key", r"\bAIza[0-9A-Za-z_\-]{35}\b"),
    ("private_key", r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
    ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\b"),
    ("assignment", r"(?im)^(\s*[\w.]*(?:secret|token|password|passwd|api_key|apikey)[\w.]*\s*[=:]\s*)(['\"]?)[^\s'\"]{6,}\2"),
]
_COMPILED = [(name, re.compile(p)) for name, p in PATTERNS]
SECRET_ENV = re.compile(r"(KEY|TOKEN|SECRET|PASSWORD)", re.I)


def _env_values() -> list[str]:
    return [v for k, v in os.environ.items() if SECRET_ENV.search(k) and len(v) >= 8]


def redact(text: str) -> tuple[str, int]:
    """Returns (scrubbed_text, number_of_redactions)."""
    count = 0
    for value in _env_values():
        if value in text:
            count += text.count(value)
            text = text.replace(value, "[REDACTED:env]")
    for name, rx in _COMPILED:
        if name == "assignment":
            text, n = rx.subn(lambda m: f"{m.group(1)}[REDACTED:{name}]", text)
        else:
            text, n = rx.subn(f"[REDACTED:{name}]", text)
        count += n
    return text, count


def scrubbed_env() -> dict[str, str]:
    """Environment for child processes: secrets removed, so a command can't read your API key."""
    return {k: v for k, v in os.environ.items() if not SECRET_ENV.search(k)}
