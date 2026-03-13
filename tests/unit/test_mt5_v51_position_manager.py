import asyncio

from agents.mt5_v51_position_manager import MT5V51PositionManagerAgent


def test_mt5_v51_position_manager_normalizes_actions(monkeypatch) -> None:
    agent = MT5V51PositionManagerAgent(
        api_key="test",
        model="deepseek/deepseek-v3.2",
        base_url="https://openrouter.ai/api/v1",
    )

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return '{"decisions":[{"ticket_id":"1","decision":"exit","confidence":0.8,"reason":"structure broke"}]}'

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "1"}]}))

    assert result.decision_batch.decisions[0].action == "close_ticket"


def test_mt5_v51_position_manager_falls_back_to_hold(monkeypatch) -> None:
    agent = MT5V51PositionManagerAgent(
        api_key="test",
        model="deepseek/deepseek-v3.2",
        base_url="https://openrouter.ai/api/v1",
    )

    async def _fake_call_model(prompt: str) -> str:
        del prompt
        return "bad-json"

    monkeypatch.setattr(agent, "_call_model", _fake_call_model)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "abc"}]}))

    assert result.decision_batch.decisions[0].action == "hold"
