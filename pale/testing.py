"""A scripted stand-in for the model, so the loop can be tested with no API key and no cost.

    model = FakeModel([
        say_and_call("Reading.", ("read_file", {"path": "a.txt"})),
        say("Done."),
    ])
    agent = Agent(settings, model=model, approver=approve_all)

Each script entry is a Reply, or a function (request) -> Reply for assertions on what was sent.
"""

from __future__ import annotations

import itertools
from types import SimpleNamespace
from typing import Callable

from .model import Reply

_ids = itertools.count(1)


def usage(inp: int = 100, out: int = 20) -> SimpleNamespace:
    return SimpleNamespace(input_tokens=inp, output_tokens=out, cache_creation_input_tokens=0, cache_read_input_tokens=0)


def say(text: str, stop: str = "end_turn") -> Reply:
    return Reply([{"type": "text", "text": text}], stop, usage(), "fake")


def say_and_call(text: str, *calls: tuple[str, dict], stop: str = "tool_use") -> Reply:
    blocks: list[dict] = [{"type": "text", "text": text}] if text else []
    for name, inp in calls:
        blocks.append({"type": "tool_use", "id": f"toolu_{next(_ids):04d}", "name": name, "input": inp})
    return Reply(blocks, stop, usage(), "fake")


def call(name: str, inp: dict) -> Reply:
    return say_and_call("", (name, inp))


class FakeModel:
    def __init__(self, script: list[Reply | Callable[[dict], Reply]]):
        self.script = list(script)
        self.requests: list[dict] = []

    def call(self, request: dict, on_delta) -> Reply:
        # snapshot messages: the agent keeps appending to the same list afterwards
        self.requests.append({**request, "messages": [dict(m) for m in request["messages"]]})
        if not self.script:
            return say("(script exhausted)")
        step = self.script.pop(0)
        reply = step(request) if callable(step) else step
        for b in reply.content:
            if b.get("type") == "text":
                on_delta("text", b["text"])
        return reply
