import asyncio

from agents.mt5_v60_entry_analyst import MT5V60EntryAnalystAgent


def test_mt5_v60_entry_analyst_normalizes_aliases(monkeypatch) -> None:
    agent = MT5V60EntryAnalystAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    async def _fake_complete_json(**kwargs) -> str:
        return '{"action":"buy","confidence":0.72,"rationale":"clean trend","thesis_tags":"trend","risk_fraction":0.004,"sl":70100,"tp":70180,"context_signature":"bull|bull|bull|tight"}'

    monkeypatch.setattr(agent._client, "complete_json", _fake_complete_json)
    result = asyncio.run(agent.analyze({"symbol": "EURUSD@"}))

    assert result.decision.action == "enter_long"
    assert result.decision.stop_loss_price == 70100
    assert result.decision.take_profit_price == 70180
    assert result.decision.requested_risk_fraction == 0.004
    assert result.decision.thesis_tags == ["trend"]


def test_mt5_v60_entry_analyst_prompt_mentions_v6_structure() -> None:
    agent = MT5V60EntryAnalystAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    prompt = agent.build_prompt({"symbol": "EURUSD@", "screenshot": {"capture_ok": True}})

    assert "recent_bars.3m" in prompt
    assert "1m and 2m" in prompt
    assert "The screenshot carries more weight" in prompt
    assert "If the picture looks choppy or range-bound, default to hold" in prompt
    assert "Take profit distance must stay realistic and within 1.0R" in prompt
    assert "0.005 means 0.5% of current total balance" in prompt
    assert "enter without broker-side TP/SL" in prompt
    assert "Screenshot metadata" in prompt
    assert agent.prompt_version == "v6.0_multimodal_v2"
