"""Model access. The Agent talks to a `ModelClient`; tests swap in `FakeModel`.

AnthropicModel uses the beta Messages stream so it can turn on, per model:
  - server-side compaction   (compact-2026-01-12): long sessions summarize themselves on the server
  - refusal fallbacks        (server-side-fallback-2026-07-01, fallbacks="default")
  - automatic prompt caching (top-level cache_control)
  - effort + adaptive thinking with summarized display, streamed to the UI
History is append-only: the harness never rewrites earlier turns (required for preserved thinking).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

ADAPTIVE_MODELS = {
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8",
    "claude-opus-4-7", "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
    "claude-haiku-5-5",
}
FALLBACK_MODELS = {"claude-fable-5-1", "claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5"}
COMPACTION_MODELS = ADAPTIVE_MODELS
WEB_SEARCH_MODELS = {
    "claude-fable-5-1", "claude-fable-5", "claude-opus-5-5", "claude-opus-5", "claude-opus-4-8", "claude-opus-4-7",
    "claude-opus-4-6", "claude-sonnet-5-5", "claude-sonnet-5", "claude-sonnet-4-6",
}


@dataclass
class Reply:
    """What the agent needs from one model call, independent of SDK types."""

    content: list[dict]  # serialized content blocks, appended to history verbatim
    stop_reason: str
    usage: Any
    model: str
    stop_details: Any = None
    extra: dict = field(default_factory=dict)


class ModelClient(Protocol):
    def call(self, request: dict, on_delta: Callable[[str, str], None]) -> Reply: ...


def build_request(*, model: str, system: str, tools: list[dict], messages: list[dict], max_tokens: int,
                  effort: str, compaction: bool, fallbacks: bool, cache: bool, web_search: int = 0) -> dict:
    req: dict = {
        "model": model,
        "max_tokens": max_tokens,
        "system": [{"type": "text", "text": system}],
        "tools": [{**t, "eager_input_streaming": True} for t in tools],  # client tools only
        "messages": messages,
        "betas": [],
    }
    if web_search and model in WEB_SEARCH_MODELS:
        req["tools"].append({"type": "web_search_20260209", "name": "web_search", "max_uses": web_search})
    if model in ADAPTIVE_MODELS:
        req["thinking"] = {"type": "adaptive", "display": "summarized"}
        req["output_config"] = {"effort": effort}
    if cache:
        req["cache_control"] = {"type": "ephemeral"}
    if compaction and model in COMPACTION_MODELS:
        req["betas"].append("compact-2026-01-12")
        req["context_management"] = {"edits": [{"type": "compact_20260112"}]}
    if fallbacks and model in FALLBACK_MODELS:
        req["betas"].append("server-side-fallback-2026-07-01")
        req["fallbacks"] = "default"
    if not req["betas"]:
        req.pop("betas")
    return req


def dump_block(block: Any) -> dict:
    if isinstance(block, dict):
        return block
    return block.model_dump(mode="json", exclude_none=True)


class AnthropicModel:
    def __init__(self, client=None):
        import anthropic

        self.anthropic = anthropic
        self.client = client or anthropic.Anthropic()

    def call(self, request: dict, on_delta: Callable[[str, str], None]) -> Reply:
        req = dict(request)
        api = self.client.beta.messages if "betas" in req else self.client.messages
        last_err: Exception | None = None
        for _attempt in range(3):  # eager input streaming: unparseable tool JSON surfaces as ValueError; re-issue
            try:
                with api.stream(**req) as stream:
                    for ev in stream:
                        if ev.type == "content_block_delta":
                            d = ev.delta
                            if d.type == "text_delta":
                                on_delta("text", d.text)
                            elif d.type == "thinking_delta" and d.thinking:
                                on_delta("thinking", d.thinking)
                    msg = stream.get_final_message()
                return Reply(
                    content=[dump_block(b) for b in msg.content],
                    stop_reason=msg.stop_reason or "end_turn",
                    usage=msg.usage,
                    model=getattr(msg, "model", req["model"]),
                    stop_details=getattr(msg, "stop_details", None),
                )
            except ValueError as e:
                last_err = e
                on_delta("warning", "malformed tool input from the model; re-issuing the request\n")
        raise RuntimeError(f"model kept producing unparseable tool input: {last_err}")
