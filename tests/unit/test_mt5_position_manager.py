import asyncio

from agents.mt5_position_manager import MT5PositionManagerAgent


def test_position_manager_normalizes_actions(monkeypatch) -> None:
    agent = MT5PositionManagerAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return '{"decisions":[{"ticket_id":"1","decision":"partial","confidence":0.8,"reason":"lock profit"}]}'

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "1"}]}))

    assert result.decision_batch.decisions[0].action == "take_partial_50"


def test_position_manager_falls_back_to_hold(monkeypatch) -> None:
    agent = MT5PositionManagerAgent(api_key="test", model="gpt-5-mini", base_url="https://api.openai.com/v1")

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return "bad-json"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "abc"}]}))

    assert result.decision_batch.decisions[0].action == "hold"
