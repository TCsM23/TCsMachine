import pytest

from pale import config
from pale.agent import Agent
from pale.events import approve_all


@pytest.fixture
def settings(tmp_path, monkeypatch):
    for var in config.ENV_OVERRIDES:
        monkeypatch.delenv(var, raising=False)
    s = config.load(tmp_path, mode="autonomous", budget_session_usd=100, budget_daily_usd=100)
    s.ensure_dirs()
    return s


@pytest.fixture
def make_agent(settings):
    def make(model, **kw):
        kw.setdefault("approver", approve_all)
        return Agent(settings, model=model, **kw)

    return make


def last_results(model):
    """tool_result blocks the model received on its most recent call."""
    msg = model.requests[-1]["messages"][-1]
    return [b for b in msg["content"] if b.get("type") == "tool_result"]
