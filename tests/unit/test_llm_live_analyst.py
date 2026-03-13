import asyncio

from agents.llm_live_analyst import LLMLiveAnalystAgent


def test_llm_live_analyst_parses_valid_json(monkeypatch) -> None:
    agent = LLMLiveAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(prompt: str) -> str:
        assert "Context packet" in prompt
        return (
            '{"action":"buy","confidence":0.72,"rationale":"trend continuation",'
            '"risk_fraction_equity":0.01,"take_profit_r":1.5,"thesis_tags":["trend"]}'
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "ETH/USD"}))

    assert result.decision.action == "buy"
    assert result.decision.take_profit_r == 1.5


def test_llm_live_analyst_falls_back_on_invalid_json(monkeypatch) -> None:
    agent = LLMLiveAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(_: str) -> str:
        return "not-json"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "ETH/USD"}))

    assert result.decision.action == "do_nothing"
    assert "invalid JSON" in result.decision.rationale


def test_llm_live_analyst_accepts_decision_alias(monkeypatch) -> None:
    agent = LLMLiveAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(_: str) -> str:
        return '{"decision":"wait","confidence":0.4,"rationale":"stand down","thesis_tags":["flat"]}'

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "ETH/USD"}))

    assert result.decision.action == "do_nothing"


def test_llm_live_analyst_accepts_runtime_alias_fields(monkeypatch) -> None:
    agent = LLMLiveAnalystAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(_: str) -> str:
        return (
            '{"decision":"buy","confidence":0.6,"reason":"bullish continuation",'
            '"risk_fraction":0.005,"take_profit_r":1.0,"thesis_tags":["bullish_ema"]}'
        )

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"symbol": "ETH/USD"}))

    assert result.decision.action == "buy"
    assert result.decision.rationale == "bullish continuation"
    assert result.decision.risk_fraction_equity == 0.005
