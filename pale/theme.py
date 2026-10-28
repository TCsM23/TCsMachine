"""Terminal look: bone-white text, ash greys, one rust accent for danger. Square boxes, no rounded corners."""

from __future__ import annotations

import random

from rich import box
from rich.console import Console
from rich.theme import Theme

BONE = "#dcd6c0"
ASH = "#8f8a78"
DUST = "#5f5b4f"
RUST = "#c4553b"
OCHRE = "#c9a95c"
MOSS = "#8fa37a"

THEME = Theme({
    "bone": BONE, "ash": ASH, "dust": DUST, "rust": RUST, "ochre": OCHRE, "moss": MOSS,
    "unit": f"bold {BONE}", "op": f"bold {OCHRE}", "tool": f"{BONE}", "think": f"italic {DUST}",
    "risk.low": MOSS, "risk.medium": OCHRE, "risk.high": f"bold {RUST}", "risk.forbidden": f"bold reverse {RUST}",
})

BOX = box.SQUARE

BANNER = r"""
 ██████╗  █████╗ ██╗     ███████╗
 ██╔══██╗██╔══██╗██║     ██╔════╝
 ██████╔╝███████║██║     █████╗
 ██╔═══╝ ██╔══██║██║     ██╔══╝
 ██║     ██║  ██║███████╗███████╗
 ╚═╝     ╚═╝  ╚═╝╚══════╝╚══════╝"""

TAGLINE = "OPERATOR SUPPORT HARNESS  //  WHITE CITY SECTOR"

BOOT_LINES = [
    "The city is quiet. The sandbox is sealed.",
    "Dust settles on the towers. Systems nominal.",
    "No signal from the surface. Awaiting directive.",
    "Every action is recorded. Nothing is forgotten.",
    "The white streets are empty. The unit is not.",
    "Memory banks intact. Black box recording.",
]

console = Console(theme=THEME, highlight=False)


def boot_line() -> str:
    return random.choice(BOOT_LINES)


def rule(label: str = "") -> None:
    console.rule(f"[dust]{label}[/]" if label else "", style="dust", characters="─")
