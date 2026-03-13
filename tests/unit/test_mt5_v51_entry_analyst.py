import asyncio

from agents.mt5_v51_entry_analyst import MT5V51EntryAnalystAgent


def test_mt5_v51_entry_analyst_normalizes_buy_alias(monkeypatch) -> None:
    agent = MT5V51EntryAnalystAgent(
        api_key="test",
        model="deepseek/deepseek-v3.2",
        base_url="https://openrouter.ai/api/v1",
    )

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return (
            '{"decision":"buy","confidence":0.71,"reason":"trend alignment","risk_fraction":0.004,'
            '"thesis_tags":"trend","context_signature":"bull|bull|bull|tight"}'
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "BTCUSD"}))

    assert result.decision.action == "enter_long"
    assert result.decision.requested_risk_fraction == 0.004
    assert result.decision.thesis_tags == ["trend"]


def test_mt5_v51_entry_analyst_falls_back_on_invalid_json(monkeypatch) -> None:
    agent = MT5V51EntryAnalystAgent(
        api_key="test",
        model="deepseek/deepseek-v3.2",
        base_url="https://openrouter.ai/api/v1",
    )

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return "not-json"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "BTCUSD"}))

    assert result.decision.action == "hold"
    assert result.decision.confidence == 0.0


def test_mt5_v51_entry_analyst_prompt_uses_scalper_language() -> None:
    agent = MT5V51EntryAnalystAgent(
        api_key="test",
        model="deepseek/deepseek-v3.2",
        base_url="https://openrouter.ai/api/v1",
    )

    prompt = agent.build_prompt({"symbol": "BTCUSD"})

    assert "20s" in prompt
    assert "1m as primary" in prompt
    assert "long_trigger_ready" in prompt
    assert "without waiting for perfect 5m agreement" in prompt
