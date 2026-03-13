import asyncio

from agents.mt5_entry_analyst import MT5EntryAnalystAgent


def test_entry_analyst_normalizes_buy_alias(monkeypatch) -> None:
    agent = MT5EntryAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return (
            '{"decision":"buy","confidence":0.71,"reason":"trend alignment","risk_fraction":0.004,'
            '"thesis_tags":"trend","context_signature":"bull|bull|bull|tight"}'
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "EURUSD"}))

    assert result.decision.action == "enter_long"
    assert result.decision.requested_risk_fraction == 0.004
    assert result.decision.thesis_tags == ["trend"]


def test_entry_analyst_falls_back_on_invalid_json(monkeypatch) -> None:
    agent = MT5EntryAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return "not-json"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "EURUSD"}))

    assert result.decision.action == "hold"
    assert result.decision.confidence == 0.0
