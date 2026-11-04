"""Tools about the unit itself: memory, skills, plan, schedule, subagents, asking the operator."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from ..permissions import Risk
from .base import Tool, ToolError


class Remember(Tool):
    name = "remember"
    description = (
        "Save or update a long-term memory that persists across sessions: operator preferences, "
        "project facts, lessons learned. One subject per memory; update an existing name rather than "
        "duplicating. Never store secrets."
    )
    mutates = True
    parallel_safe = False

    class Input(BaseModel):
        name: str = Field(description="short-kebab-case subject, e.g. 'operator-preferences'")
        description: str = Field(description="one line, used to decide relevance later")
        content: str

    def risk(self, inp, ctx):
        return Risk("low", ["writes the unit's own memory"])

    def preview(self, inp, ctx):
        return f"memory/{inp.name}.md\n{inp.description}\n\n{inp.content}"

    def run(self, inp, ctx):
        note = ctx.agent.memory.put(inp.name, inp.description, inp.content, source=ctx.agent.session_id)
        return f"remembered '{note.name}'"


class Recall(Tool):
    name = "recall"
    description = "Search long-term memory by keywords, or load one memory by exact name. Returns full bodies."

    class Input(BaseModel):
        query: str

    def run(self, inp, ctx):
        mem = ctx.agent.memory
        exact = mem.get(inp.query) if inp.query.strip() else None
        notes = [exact] if exact else mem.search(inp.query)
        if not notes:
            return "nothing in memory matches"
        return "\n\n".join(f"## {n.name}\n{n.description}\n\n{n.body}" for n in notes)


class Forget(Tool):
    name = "forget"
    description = "Delete a long-term memory that turned out to be wrong or obsolete."
    mutates = True

    class Input(BaseModel):
        name: str

    def run(self, inp, ctx):
        return "forgotten" if ctx.agent.memory.delete(inp.name) else f"no memory named {inp.name}"


class UseSkill(Tool):
    name = "use_skill"
    description = "Load the full instructions of an approved skill listed in your system prompt, then follow them."

    class Input(BaseModel):
        name: str

    def run(self, inp, ctx):
        s = ctx.agent.skills.get(inp.name)
        if not s:
            raise ToolError(f"no approved skill named {inp.name}")
        return f"# skill: {s.name}\n{s.body}"


class ProposeSkill(Tool):
    name = "propose_skill"
    description = (
        "Propose a reusable skill (a procedure you would repeat) after finishing a task that needed "
        "several steps. It goes to quarantine and only becomes usable after the operator approves it."
    )
    mutates = True

    class Input(BaseModel):
        name: str
        description: str = Field(description="one line: when to use this skill")
        body: str = Field(description="markdown instructions: steps, commands, pitfalls")

    def risk(self, inp, ctx):
        return Risk("low", ["quarantined until the operator approves"])

    def run(self, inp, ctx):
        n = ctx.agent.skills.propose(inp.name, inp.description, inp.body, ctx.agent.session_id)
        ctx.agent.emit("status", message=f"skill proposal '{n.name}' quarantined; approve with: pale skills approve {n.name}")
        return f"skill '{n.name}' proposed and quarantined. The operator must approve it before it can be used."


class TodoItem(BaseModel):
    text: str
    status: Literal["pending", "active", "done"] = "pending"


class Todo(Tool):
    name = "todo"
    description = (
        "Maintain your plan for the current directive as a checklist (replaces the whole list). Use it "
        "for any task with 3+ steps; keep exactly one item 'active' while working."
    )

    class Input(BaseModel):
        items: list[TodoItem]

    def arg(self, inp):
        active = next((i.text for i in inp.items if i.status == "active"), "")
        done = sum(i.status == "done" for i in inp.items)
        return f"{done}/{len(inp.items)} done" + (f" · {active}" if active else "")

    def run(self, inp, ctx):
        ctx.agent.todos = [i.model_dump() for i in inp.items]
        ctx.agent.emit("todo", items=ctx.agent.todos)
        mark = {"pending": "[ ]", "active": "[>]", "done": "[x]"}
        return "\n".join(f"{mark[i.status]} {i.text}" for i in inp.items) or "(empty plan)"


class Schedule(Tool):
    name = "schedule_directive"
    description = (
        "Schedule a directive to run later without the operator present. spec: 'every 30m', "
        "'every 2h', 'daily 09:00', 'weekdays 08:30', 'once 2026-11-01 09:00', or 'watch <glob>' "
        "(e.g. 'watch src/**/*.py') to run whenever matching sandbox files change. Unattended runs "
        "cannot do anything that needs approval."
    )
    mutates = True

    class Input(BaseModel):
        spec: str
        directive: str
        name: str = ""

    def arg(self, inp):
        return inp.spec

    def risk(self, inp, ctx):
        return Risk("medium", ["runs later without the operator watching"])

    def run(self, inp, ctx):
        try:
            t = ctx.agent.schedule.add(inp.spec, inp.directive, inp.name)
        except ValueError as e:
            raise ToolError(str(e))
        return f"scheduled {t['id']} '{t['name']}', next run {t['next']}"


class DispatchUnit(Tool):
    name = "dispatch_unit"
    description = (
        "Send a support unit to investigate something (search a codebase, read many files, summarize) "
        "and report back. It runs on a cheaper model with its own context, so your context stays small. "
        "Leave `unit` empty for the default read-only unit, or name a specialist from your system prompt."
    )

    class Input(BaseModel):
        task: str = Field(description="complete, self-contained instructions; the unit sees nothing else")
        unit: str = Field("", description="specialist unit name, or empty for the default support unit")

    def arg(self, inp):
        return (f"[{inp.unit}] " if inp.unit else "") + inp.task

    def run(self, inp, ctx):
        return ctx.agent.spawn_subagent(inp.task, inp.unit)


class SubmitPlan(Tool):
    name = "submit_plan"
    description = (
        "Plan mode only. Investigate with read-only tools first, then submit your plan. The operator "
        "reads it and approves or declines with a note. Approved: the mode switches and you carry the "
        "plan out. Declined: revise the plan from the note and submit again. Nothing can change before this."
    )
    mutates = True
    parallel_safe = False
    always_ask = True

    class Input(BaseModel):
        summary: str = Field(description="markdown: the approach, which files change and how, how you'll verify, risks")
        steps: list[str] = Field(description="the checklist you will follow, one short line per step")

    def arg(self, inp):
        return inp.summary.strip().splitlines()[0][:120] if inp.summary.strip() else "plan"

    def risk(self, inp, ctx):
        return Risk("low", ["nothing changes until you approve"])

    def preview(self, inp, ctx):
        steps = "\n".join(f"{i}. {s}" for i, s in enumerate(inp.steps, 1))
        return f"{inp.summary.strip()}\n\n**Steps**\n{steps}"

    def run(self, inp, ctx):
        return ctx.agent.plan_approved(inp.steps)

    def dry(self, inp, ctx):
        return self.run(inp, ctx)


class AskOperator(Tool):
    name = "ask_operator"
    description = "Ask the operator a question when you are genuinely blocked on a decision only they can make."

    class Input(BaseModel):
        question: str

    def run(self, inp, ctx):
        return ctx.agent.ask(inp.question)


TOOLS = [Remember(), Recall(), Forget(), UseSkill(), ProposeSkill(), Todo(), Schedule(), DispatchUnit(), SubmitPlan(),
         AskOperator()]
