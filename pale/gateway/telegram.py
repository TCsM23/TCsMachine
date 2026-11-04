"""Telegram gateway (Hermes-style "same agent, many surfaces").

Raw Bot API over long polling, so no webhook or public URL is needed.
Security:
  - refuses to start without an allow-list of numeric Telegram user ids
  - messages from anyone else are dropped and logged to the black box
  - approvals arrive as inline buttons; only an allowed user can press them
  - "always" approvals are not offered here: on a phone it's too easy to tap the wrong one
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import httpx

from ..agent import Agent
from ..blackbox import BlackBox
from ..config import Mode, Settings
from ..events import ApprovalDecision, ApprovalRequest, Event

API = "https://api.telegram.org/bot{token}/{method}"
LIMIT = 3900


class Chat:
    """One Telegram chat = one agent session, plus the status message we keep editing."""

    def __init__(self, gw: "TelegramGateway", chat_id: int):
        self.gw = gw
        self.chat_id = chat_id
        self.agent = self._new_agent()
        self.status_id: int | None = None
        self.lines: list[str] = []
        self.last_edit = 0.0
        self.waiting: dict[str, tuple[threading.Event, dict]] = {}
        self.question: tuple[threading.Event, dict] | None = None

    def _new_agent(self) -> Agent:
        return Agent(self.gw.settings.model_copy(), approver=self.approve, sink=self.on_event, ask_fn=self.ask,
                     surface=f"telegram:{self.chat_id}", model=self.gw.model)

    def reset(self) -> None:
        self.agent = self._new_agent()

    # ---- events -> one live status message
    def on_event(self, ev: Event) -> None:
        d = ev.data
        line = None
        if ev.kind == "tool_call":
            line = f"■ {d['tool']} {d['arg'][:60]}"
        elif ev.kind == "denied":
            line = f"✕ {d['tool']}: {d['reason'][:80]}"
        elif ev.kind == "warning" and not d["message"].startswith("malformed"):
            line = f"⚠ {d['message'][:120]}"
        elif ev.kind == "error":
            line = f"✕ {d['message'][:200]}"
        elif ev.kind == "mode":
            line = f"mode → {d['mode'].upper()}"
        elif ev.kind == "job":
            line = f"▶ job {d['id']} · {'running' if d['running'] else d['stopped'] or 'exit ' + str(d['code'])}"
        elif ev.kind == "review":
            line = "■ review: no findings" if d["clean"] else f"⚠ review findings:\n{d['text'][:600]}"
        if line:
            self.lines.append(line)
            self.flush_status()

    def flush_status(self, force: bool = False) -> None:
        if not self.lines or (not force and time.time() - self.last_edit < 1.5):
            return
        text = "UNIT LOG\n" + "\n".join(self.lines[-25:])
        if self.status_id is None:
            r = self.gw.send(self.chat_id, text)
            self.status_id = r.get("message_id") if r else None
        else:
            self.gw.call("editMessageText", chat_id=self.chat_id, message_id=self.status_id, text=text[:LIMIT])
        self.last_edit = time.time()

    # ---- approvals and questions block the agent thread until a button / reply arrives
    def approve(self, req: ApprovalRequest) -> ApprovalDecision:
        key = uuid.uuid4().hex[:10]
        done, box = threading.Event(), {}
        self.waiting[key] = (done, box)
        if req.tool == "submit_plan":
            head = "PLAN REVIEW (nothing changes until you approve)"
            preview = f"\n\n{req.preview[:3000]}"
        else:
            head = f"AUTHORIZATION REQUIRED [{req.risk.upper()}]\n{req.summary}\n{' · '.join(r for r in req.reasons if r)}"
            preview = f"\n\n{req.preview[:1500]}" if req.preview else ""
        self.gw.send(
            self.chat_id,
            f"{head}{preview}",
            reply_markup={"inline_keyboard": [[
                {"text": "■ AUTHORIZE", "callback_data": f"ap:{key}:y"},
                {"text": "✕ DENY", "callback_data": f"ap:{key}:n"},
            ]]},
        )
        if not done.wait(900):
            self.waiting.pop(key, None)
            return ApprovalDecision(False, "no answer from the operator within 15 minutes")
        return ApprovalDecision(box.get("ok", False))

    def ask(self, question: str) -> str:
        done, box = threading.Event(), {}
        self.question = (done, box)
        self.gw.send(self.chat_id, f"UNIT REQUESTS INPUT\n{question}\n\n(reply with your answer)")
        done.wait(900)
        self.question = None
        return box.get("text", "")

    def run(self, text: str) -> None:
        self.status_id, self.lines = None, []
        try:
            out = self.agent.run(text)
        except RuntimeError as e:
            self.gw.send(self.chat_id, f"✕ {e}")
            return
        self.flush_status(force=True)
        m = self.agent.budget.meter
        report = f"{out}\n\n── {self.agent.steps} step(s) · ${m.usd:.4f} session"
        for i in range(0, len(report), LIMIT):
            self.gw.send(self.chat_id, report[i : i + LIMIT])


class TelegramGateway:
    def __init__(self, settings: Settings, model=None):
        settings.ensure_dirs()
        self.model = model
        self.settings = settings
        self.token = os.environ.get(settings.telegram.token_env, "")
        if not self.token:
            raise SystemExit(f"set {settings.telegram.token_env} to your bot token (from @BotFather)")
        self.allowed = set(settings.telegram.allowed_user_ids)
        if not self.allowed:
            raise SystemExit("telegram.allowed_user_ids is empty in pale.toml. Refusing to expose the unit to everyone.")
        self.http = httpx.Client(timeout=40)
        self.chats: dict[int, Chat] = {}
        self.blackbox = BlackBox(settings.bunker / "blackbox.jsonl")

    def call(self, method: str, **params) -> dict | None:
        try:
            r = self.http.post(API.format(token=self.token, method=method), json=params)
            data = r.json()
            return data.get("result") if data.get("ok") else None
        except (httpx.HTTPError, ValueError):
            return None

    def send(self, chat_id: int, text: str, **extra) -> dict | None:
        return self.call("sendMessage", chat_id=chat_id, text=text[:LIMIT], **extra)

    def notify_report(self, task: dict, report: str) -> None:
        for uid in self.allowed:
            self.send(uid, f"SCHEDULED DIRECTIVE · {task['name']}\n\n{report[:3500]}")

    def chat(self, chat_id: int) -> Chat:
        if chat_id not in self.chats:
            self.chats[chat_id] = Chat(self, chat_id)
        return self.chats[chat_id]

    def handle(self, upd: dict) -> None:
        if "callback_query" in upd:
            cq = upd["callback_query"]
            if cq["from"]["id"] not in self.allowed:
                return
            _, key, ans = (cq.get("data") or "x:x:x").split(":", 2)
            chat = self.chat(cq["message"]["chat"]["id"])
            if key in chat.waiting:
                done, box = chat.waiting.pop(key)
                box["ok"] = ans == "y"
                done.set()
                self.call("answerCallbackQuery", callback_query_id=cq["id"], text="authorized" if ans == "y" else "denied")
                self.call("editMessageReplyMarkup", chat_id=chat.chat_id, message_id=cq["message"]["message_id"], reply_markup={"inline_keyboard": []})
            return
        msg = upd.get("message") or {}
        user = (msg.get("from") or {}).get("id")
        text = (msg.get("text") or "").strip()
        if not text or user is None:
            return
        if user not in self.allowed:
            self.blackbox.record("gateway_rejected", "", surface="telegram", user=user, text=text[:200])
            return
        chat = self.chat(msg["chat"]["id"])
        if chat.question:
            done, box = chat.question
            box["text"] = text
            done.set()
            return
        if text.startswith("/"):
            return self.command(chat, text)
        if chat.agent._busy.locked():
            self.send(chat.chat_id, "unit is still executing. /halt to interrupt.")
            return
        threading.Thread(target=chat.run, args=(text,), daemon=True).start()

    def command(self, chat: Chat, text: str) -> None:
        cmd, *args = text.split()
        cmd = cmd.split("@")[0].lower()
        if cmd in ("/start", "/help"):
            self.send(chat.chat_id, "PALE // unit link established.\nSend a directive as a message.\n"
                                    "/new · /halt · /undo · /cost · /mode <observe|plan|ask|edits|autonomous> · /dry <on|off>")
        elif cmd == "/new":
            chat.reset()
            self.send(chat.chat_id, "new session.")
        elif cmd == "/halt":
            chat.agent.interrupt()
            self.send(chat.chat_id, "halt signal sent.")
        elif cmd == "/undo":
            r = chat.agent.checkpoints.undo()
            self.send(chat.chat_id, f"restored: {', '.join(r['restored'])}" if r["id"] else "nothing to undo")
        elif cmd == "/cost":
            m = chat.agent.budget.meter
            self.send(chat.chat_id, f"session ${m.usd:.4f} · today ${chat.agent.budget.ledger.today():.4f}")
        elif cmd == "/mode" and args and args[0] in Mode.__members__:
            chat.agent.set_mode(args[0])
            self.send(chat.chat_id, f"mode → {args[0].upper()}")
        elif cmd == "/dry" and args:
            chat.agent.settings.dry_run = args[0] == "on"
            self.send(chat.chat_id, f"dry run {'ON' if chat.agent.settings.dry_run else 'off'}")
        else:
            self.send(chat.chat_id, "unknown command. /help")

    def run_forever(self) -> None:
        offset = 0
        while True:
            updates = self.call("getUpdates", offset=offset, timeout=30, allowed_updates=["message", "callback_query"]) or []
            for upd in updates:
                offset = upd["update_id"] + 1
                try:
                    self.handle(upd)
                except Exception as e:  # never let one bad update kill the gateway
                    self.blackbox.record("gateway_error", "", error=repr(e))
            if not updates:
                time.sleep(0.5)
