import asyncio
from decimal import Decimal

from agents.mt5_v60_position_manager import MT5V60PositionManagerAgent


def test_mt5_v60_position_manager_normalizes_commands(monkeypatch) -> None:
    agent = MT5V60PositionManagerAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    async def _fake_complete_json(**kwargs) -> str:
        return (
            '{"decisions":[{"ticket_id":"1001","confidence":0.61,"rationale":"tighten and scale",'
            '"commands":{"action":"partial","fraction":0.5},"visual_context_update":{"bias":"bearish"}}]}'
        )

    monkeypatch.setattr(agent._client, "complete_json", _fake_complete_json)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "1001"}]}))

    assert result.decision_batch.decisions[0].commands[0].action == "close_partial"
    assert result.decision_batch.decisions[0].commands[0].close_fraction == 0.5
    assert result.decision_batch.decisions[0].visual_context_update == {"bias": "bearish"}


def test_mt5_v60_position_manager_prompt_describes_naked_entry_handoff() -> None:
    agent = MT5V60PositionManagerAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    prompt = agent.build_prompt({"tickets": [{"ticket_id": "1001", "stop_loss": None, "take_profit": None}]})

    assert "entered naked on purpose" in prompt
    assert "ticket.initial_stop_loss and ticket.initial_take_profit are internal Analyzer anchors" in prompt


def test_mt5_v60_position_manager_prompt_mentions_auto_first_protection_review() -> None:
    agent = MT5V60PositionManagerAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    prompt = agent.build_prompt({"tickets": [{"ticket_id": "1001", "first_protection_review_pending": True}]})

    assert "auto-attached immediately after a naked fill" in prompt
    assert "breakeven" in prompt
    assert "partials" in prompt


def test_mt5_v60_position_manager_normalizes_string_visual_context_update(monkeypatch) -> None:
    agent = MT5V60PositionManagerAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    async def _fake_complete_json(**kwargs) -> str:
        return (
            '{"decisions":[{"ticket_id":"61640705","confidence":0.62,'
            '"rationale":"Place first protection.","commands":[{"action":"modify_ticket","stop_loss_price":70046.5,"take_profit_price":69962.49,"close_fraction":null}],'
            '"visual_context_update":"First protection placed for BTCUSD short."}]}'
        )

    monkeypatch.setattr(agent._client, "complete_json", _fake_complete_json)
    result = asyncio.run(agent.analyze({"tickets": [{"ticket_id": "61640705"}]}))

    decision = result.decision_batch.decisions[0]
    assert decision.commands[0].action == "modify_ticket"
    assert decision.commands[0].stop_loss_price == Decimal("70046.5")
    assert decision.commands[0].take_profit_price == Decimal("69962.49")
    assert decision.visual_context_update == {"summary": "First protection placed for BTCUSD short."}


def test_mt5_v60_position_manager_upgrades_hold_with_tp_change_to_modify_ticket(monkeypatch) -> None:
    agent = MT5V60PositionManagerAgent(
        api_key="test",
        model="gpt-5-nano",
        base_url="https://api.openai.com/v1",
    )

    async def _fake_complete_json(**kwargs) -> str:
        return (
            '{"decisions":[{"ticket_id":"61690195","confidence":0.55,'
            '"rationale":"Keep the trade but add the missing TP.",'
            '"commands":[{"action":"hold","stop_loss_price":69750,"take_profit_price":70109,"close_fraction":null}],'
            '"visual_context_update":null}]}'
        )

    monkeypatch.setattr(agent._client, "complete_json", _fake_complete_json)
    result = asyncio.run(
        agent.analyze(
            {
                "tickets": [
                    {
                        "ticket_id": "61690195",
                        "stop_loss": 69750,
                        "take_profit": None,
                    }
                ]
            }
        )
    )

    decision = result.decision_batch.decisions[0]
    assert decision.commands[0].action == "modify_ticket"
    assert decision.commands[0].stop_loss_price == Decimal("69750")
    assert decision.commands[0].take_profit_price == Decimal("70109")
