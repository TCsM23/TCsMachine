"""The loop never prints. It emits events, and each surface (CLI, web, Telegram) renders them.

This is the "separate the loop from the interface" rule: one Agent, many thin adapters.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol


@dataclass
class Event:
    kind: str  # text | thinking | tool_call | tool_result | denied | status | cost | warning | error | done
    data: dict[str, Any] = field(default_factory=dict)


EventSink = Callable[[Event], None]


def null_sink(_: Event) -> None:
    pass


@dataclass
class ApprovalRequest:
    tool: str
    summary: str  # one line: what will happen
    risk: str  # low | medium | high
    reasons: list[str]
    preview: str = ""  # diff or command, shown before the operator decides
    call_id: str = ""


@dataclass
class ApprovalDecision:
    approved: bool
    note: str = ""  # optional operator message passed back to the model on denial
    always: bool = False  # approve this rule for the rest of the session


class Approver(Protocol):
    def __call__(self, req: ApprovalRequest) -> ApprovalDecision: ...


def deny_all(req: ApprovalRequest) -> ApprovalDecision:
    """Used for unattended runs (scheduler): anything that needs a human is refused, never escalated."""
    return ApprovalDecision(False, "unattended run: actions that need approval are refused")


def approve_all(req: ApprovalRequest) -> ApprovalDecision:
    return ApprovalDecision(True)
