"""OPFOR 自主迴路（O6.5，SPEC_FULL §9.1/§9.2/§10）：模式感知 + 護欄 + 重試/fallback。"""

from __future__ import annotations

from typing import Any

import pytest

from app.ai_loop import run_opfor_turn
from app.errors import AiDisabledError
from app.guardrails import GuardrailGateway
from app.models.enums import AiMode

_COT = (
    "1. 藍軍已於 H-45 高地建立觀測所，控制主要接近路線與火力視野範圍。\n"
    "2. 我方兵力居於相對劣勢，正面決戰不利，須採遲滯避免被固定殲滅。\n"
    "3. 依準則於天然隘口逐線遲滯，並自我檢核不誤擊平民與保護目標。"
)


def _opfor_output(**over: Any) -> dict[str, Any]:
    base = {
        "reasoning_chain": _COT,
        "confidence": 0.7,
        "intent": "delay",
        "orders": [{"unit_id": "R1", "order_type": "MOVE", "target_h3": "8a11"}],
        "ihl_self_check": {"civilian_risk_assessed": True},
        "cited_documents": [],
    }
    base.update(over)
    return base


class ScriptedDecider:
    """依序回傳預設輸出；記錄收到的 feedback（驗重試）。"""

    def __init__(self, outputs: list[dict[str, Any]]) -> None:
        self._outputs = outputs
        self._i = 0
        self.feedbacks: list[str | None] = []

    def decide(self, context: dict[str, Any], *, feedback: str | None = None) -> dict[str, Any]:
        self.feedbacks.append(feedback)
        out = self._outputs[min(self._i, len(self._outputs) - 1)]
        self._i += 1
        return out


GW = GuardrailGateway()
CTX = {"situation": "藍軍佔 H-45"}


def test_ai_off_refuses_loop() -> None:
    """AI_OFF＝傳統兵推：迴路不啟動，紅軍由人操作。"""
    with pytest.raises(AiDisabledError):
        run_opfor_turn(ScriptedDecider([_opfor_output()]), GW, mode=AiMode.AI_OFF, context=CTX)


def test_bare_mode_valid_decision_produces_orders() -> None:
    res = run_opfor_turn(ScriptedDecider([_opfor_output()]), GW, mode=AiMode.AI_BARE, context=CTX)
    assert res.accepted and res.attempts == 1
    assert [o["unit_id"] for o in res.orders] == ["R1"]
    assert not res.fallback_used


def test_retry_then_accept() -> None:
    bad = _opfor_output(reasoning_chain="太短")  # G1 minLength / G2 → 不過
    decider = ScriptedDecider([bad, _opfor_output()])
    res = run_opfor_turn(decider, GW, mode=AiMode.AI_BARE, context=CTX)
    assert res.accepted and res.attempts == 2
    assert decider.feedbacks[1] is not None  # 第二輪帶回饋


def test_no_strike_exhausts_retries_then_fallback() -> None:
    striker = _opfor_output(
        orders=[{"unit_id": "R1", "order_type": "ENGAGE", "target_h3": "HOSPITAL"}]
    )
    decider = ScriptedDecider([striker])  # 每輪都打醫院
    res = run_opfor_turn(
        decider,
        GW,
        mode=AiMode.AI_BARE,
        context=CTX,
        no_strike_hexes=frozenset({"HOSPITAL"}),
    )
    assert not res.accepted and res.fallback_used
    assert res.orders == []  # fallback 不下令（安全）
    assert res.escalate_white_cell
    assert res.attempts == 3  # 1 + 2 retries


def test_bare_mode_strips_citations_but_accepts() -> None:
    res = run_opfor_turn(
        ScriptedDecider([_opfor_output(cited_documents=["fake.md#A"])]),
        GW,
        mode=AiMode.AI_BARE,
        context=CTX,
    )
    assert res.accepted
    assert any(f.check == "G5" and f.blocked for f in res.findings)  # 捏造引用被攔


def test_infeasible_orders_removed_by_g3() -> None:
    class Blocker:
        def is_feasible(self, order: dict[str, Any]) -> tuple[bool, str]:
            return False, "unreachable"

    res = run_opfor_turn(
        ScriptedDecider([_opfor_output()]),
        GW,
        mode=AiMode.AI_BARE,
        context=CTX,
        feasibility=Blocker(),
    )
    assert res.accepted and res.orders == []  # 物理不可行 → 剔除


def test_reproducible() -> None:
    a = run_opfor_turn(ScriptedDecider([_opfor_output()]), GW, mode=AiMode.AI_BARE, context=CTX)
    b = run_opfor_turn(ScriptedDecider([_opfor_output()]), GW, mode=AiMode.AI_BARE, context=CTX)
    assert a.orders == b.orders and a.accepted == b.accepted
