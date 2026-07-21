"""RoleManager 佇列優先權 + adapter 批次攤銷 + latency（O6.1）。"""

from __future__ import annotations

from collections.abc import Sequence

import pytest

from matso_ai.inference.client import ChatMessage, LLMResponse
from matso_ai.inference.role_manager import AIRequest, QueueFullError, RoleManager
from matso_ai.roles import Role, UnknownRoleError


class EchoClient:
    """回傳可辨識角色的固定回應；不觸網路。"""

    def complete(self, messages: Sequence[ChatMessage], *, model: str, adapter: str) -> LLMResponse:
        return LLMResponse(
            text=f"ok:{adapter}", tokens_in=1, tokens_out=1, model=model, adapter=adapter
        )


def _roles(results) -> list[Role]:  # type: ignore[no-untyped-def]
    return [r.request.role for r in results]


def test_opfor_has_highest_queue_priority() -> None:
    mgr = RoleManager(EchoClient())
    mgr.enqueue(AIRequest(Role.AAR_ANALYST, "a"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "b"))
    mgr.enqueue(AIRequest(Role.STRATEGIC_PLANNER, "c"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "d"))

    order = _roles(mgr.process_pending())
    # OPFOR 先（兩筆），再 PLANNER，最後 AAR
    assert order == [
        Role.OPFOR_COMMANDER,
        Role.OPFOR_COMMANDER,
        Role.STRATEGIC_PLANNER,
        Role.AAR_ANALYST,
    ]


def test_fifo_within_same_role() -> None:
    mgr = RoleManager(EchoClient())
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "first", request_id="r1"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "second", request_id="r2"))
    ids = [r.request.request_id for r in mgr.process_pending()]
    assert ids == ["r1", "r2"]  # 組內維持 enqueue 順序


def test_adapter_swaps_amortized_by_grouping() -> None:
    mgr = RoleManager(EchoClient())
    # 交錯入列兩個角色各兩筆；分組後應只切換 adapter 2 次（非 4 次）
    interleaved = [
        Role.OPFOR_COMMANDER,
        Role.STRATEGIC_PLANNER,
        Role.OPFOR_COMMANDER,
        Role.STRATEGIC_PLANNER,
    ]
    for role in interleaved:
        mgr.enqueue(AIRequest(role, "x"))
    mgr.process_pending()
    assert mgr.adapter_swaps == 2  # 每角色一次，攤銷熱切換


def test_latency_uses_injected_clock() -> None:
    ticks = iter([1.0, 1.25])  # start, end → 250ms
    mgr = RoleManager(EchoClient(), clock=lambda: next(ticks))
    res = mgr.invoke(AIRequest(Role.OPFOR_COMMANDER, "x"))
    assert res.latency_ms == 250


def test_unknown_role_rejected() -> None:
    mgr = RoleManager(EchoClient(), registry={})
    with pytest.raises(UnknownRoleError):
        mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "x"))


def test_queue_full_raises_backpressure() -> None:
    """C9：佇列達上限即拋 QueueFullError（背壓，不無限成長）。"""
    mgr = RoleManager(EchoClient(), max_queue=2)
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "a"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "b"))
    with pytest.raises(QueueFullError):
        mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "c"))


def test_partial_failure_isolated_and_queue_cleared() -> None:
    """C10：單筆失敗不拖垮整批；佇列一律清空。"""

    class FlakyClient:
        def complete(self, messages, *, model, adapter):  # type: ignore[no-untyped-def]
            if "boom" in messages[1].content:
                raise RuntimeError("模型爆炸")
            return LLMResponse("ok", tokens_in=1, tokens_out=1, model=model, adapter=adapter)

    mgr = RoleManager(FlakyClient())
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "fine-1"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "boom"))
    mgr.enqueue(AIRequest(Role.OPFOR_COMMANDER, "fine-2"))
    results = mgr.process_pending()
    assert len(results) == 2  # 兩筆成功、爆炸那筆被跳過
    assert mgr.pending == 0  # 佇列清空
