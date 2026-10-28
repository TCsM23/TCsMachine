"""Cost meter and hard spending caps (per session and per day, across every surface)."""

from __future__ import annotations

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

# USD per million tokens: (input, output). Cache writes bill at 1.25x input, cache reads at 0.1x.
PRICES = {
    "claude-fable-5-1": (10.0, 50.0),
    "claude-fable-5": (10.0, 50.0),
    "claude-opus-5-5": (4.0, 20.0),
    "claude-opus-5": (5.0, 25.0),
    "claude-opus-4-8": (5.0, 25.0),
    "claude-sonnet-5-5": (2.0, 10.0),
    "claude-sonnet-5": (2.0, 10.0),
    "claude-haiku-5-5": (0.10, 0.50),
    "claude-haiku-4-5": (1.0, 5.0),
}
FALLBACK_PRICE = (10.0, 50.0)  # unknown model: assume the expensive end


def cost_of(model: str, usage) -> float:
    pin, pout = PRICES.get(model, FALLBACK_PRICE)
    g = lambda k: (getattr(usage, k, None) if not isinstance(usage, dict) else usage.get(k)) or 0  # noqa: E731
    return (
        g("input_tokens") * pin
        + g("cache_creation_input_tokens") * pin * 1.25
        + g("cache_read_input_tokens") * pin * 0.1
        + g("output_tokens") * pout
    ) / 1_000_000


class BudgetExceeded(Exception):
    pass


@dataclass
class Meter:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read: int = 0
    usd: float = 0.0


class Ledger:
    """Daily spend persisted in bunker/ledger.json, shared by CLI, web, Telegram and scheduler."""

    _lock = threading.Lock()

    def __init__(self, path: Path, daily_cap: float):
        self.path = path
        self.daily_cap = daily_cap

    def _load(self) -> dict:
        try:
            return json.loads(self.path.read_text())
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def today(self) -> float:
        return self._load().get(time.strftime("%Y-%m-%d"), 0.0)

    def add(self, usd: float) -> float:
        with self._lock:
            data = self._load()
            day = time.strftime("%Y-%m-%d")
            data[day] = round(data.get(day, 0.0) + usd, 6)
            self.path.write_text(json.dumps(data, indent=1))
            return data[day]


class Budget:
    def __init__(self, ledger: Ledger, session_cap: float):
        self.ledger = ledger
        self.session_cap = session_cap
        self.meter = Meter()

    def check(self) -> None:
        if self.meter.usd >= self.session_cap:
            raise BudgetExceeded(f"session budget spent (${self.meter.usd:.4f} of ${self.session_cap:.2f})")
        spent = self.ledger.today()
        if spent >= self.ledger.daily_cap:
            raise BudgetExceeded(f"daily budget spent (${spent:.4f} of ${self.ledger.daily_cap:.2f})")

    def charge(self, model: str, usage) -> float:
        usd = cost_of(model, usage)
        m = self.meter
        m.input_tokens += (getattr(usage, "input_tokens", 0) or 0) + (getattr(usage, "cache_creation_input_tokens", 0) or 0)
        m.output_tokens += getattr(usage, "output_tokens", 0) or 0
        m.cache_read += getattr(usage, "cache_read_input_tokens", 0) or 0
        m.usd += usd
        self.ledger.add(usd)
        return usd
