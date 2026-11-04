"""Tool contract. A tool is a Pydantic input model plus four methods:

  arg(inp)          the main argument, matched by permission rules ("run_command(git *)")
  risk(inp, ctx)    low | medium | high | forbidden, with reasons shown to the operator
  preview(inp, ctx) what the operator sees before approving (a diff, the command)
  run(inp, ctx)     do it, return text for the model
  dry(inp, ctx)     dry-run: describe what would happen, change nothing

The model never runs anything. It asks; the harness validates, scores, gates, runs, logs.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import BaseModel

from ..permissions import Risk

if TYPE_CHECKING:
    from ..agent import Agent


class ToolError(Exception):
    """An expected failure the model should see and recover from (missing file, bad edit)."""


@dataclass
class ToolContext:
    agent: "Agent"
    call_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def settings(self):
        return self.agent.settings

    @property
    def sandbox(self):
        return self.agent.sandbox


class Tool:
    name: ClassVar[str]
    description: ClassVar[str]
    Input: ClassVar[type[BaseModel]]
    mutates: ClassVar[bool] = False
    parallel_safe: ClassVar[bool] = True  # read-only tools in one turn run concurrently
    always_ask: ClassVar[bool] = False  # the operator must answer, even in dry run (submit_plan)
    pollable: ClassVar[bool] = False  # repeating the same call is normal (job_status); the loop guard ignores it

    def schema(self) -> dict:
        s = self.Input.model_json_schema()
        s.pop("title", None)
        for prop in s.get("properties", {}).values():
            prop.pop("title", None)
        return {"name": self.name, "description": self.description, "input_schema": s}

    def arg(self, inp: BaseModel) -> str:
        first = next(iter(type(inp).model_fields), None)
        return str(getattr(inp, first, "")) if first else ""

    def risk(self, inp: BaseModel, ctx: ToolContext) -> Risk:
        return Risk("medium" if self.mutates else "low")

    def preview(self, inp: BaseModel, ctx: ToolContext) -> str:
        return ""

    def run(self, inp: BaseModel, ctx: ToolContext) -> str:
        raise NotImplementedError

    def dry(self, inp: BaseModel, ctx: ToolContext) -> str:
        if not self.mutates:
            return self.run(inp, ctx)
        return f"[dry run] would call {self.name}({self.arg(inp)}). Nothing was changed."
