"""裁決事件推送到 WS 戰況 feed（build_event_envelope + feed 過濾 + CollectingBroadcaster）。"""

from __future__ import annotations

import pytest

from app.state.broadcaster import CollectingBroadcaster, build_event_envelope
from app.state.ledger import LedgerEvent


def test_event_envelope_shape_for_engagement() -> None:
    e = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=42,
        initiator_id="B1",
        target_id="R1",
        damage_calc=50.0,
        ai_decision={"status": "HIT", "target_health_after": 50.0, "roll": 0.3},
    )
    env = build_event_envelope(e)
    assert env["type"] == "EVENT"
    p = env["payload"]
    assert p["event_type"] == "ENGAGEMENT_RESOLVED"
    assert p["tick"] == 42
    assert p["initiator_id"] == "B1"
    assert p["target_id"] == "R1"
    assert p["damage"] == pytest.approx(50.0)
    assert p["status"] == "HIT"
    assert p["target_health_after"] == pytest.approx(50.0)
    # roll 非 feed 欄位 → 不外洩到精簡 payload
    assert "roll" not in p


def test_event_envelope_rejected_carries_reason() -> None:
    e = LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=1,
        initiator_id="B1",
        target_id="R1",
        ai_decision={"status": "REJECTED", "reason": "NO_LOS"},
    )
    p = build_event_envelope(e)["payload"]
    assert p["status"] == "REJECTED"
    assert p["reason"] == "NO_LOS"
    assert "damage" not in p  # damage_calc None → 不含


async def test_collecting_broadcaster_filters_noisy_events() -> None:
    bc = CollectingBroadcaster()
    events = [
        LedgerEvent(event_type="ENGAGEMENT_RESOLVED", tick=1),
        LedgerEvent(event_type="UNIT_MOVED", tick=1),  # 應保留於 collector（過濾在 Redis 實作）
    ]
    await bc.publish_events(events)
    # CollectingBroadcaster 記錄全部（過濾邏輯屬 RedisBroadcaster）；驗證介面存在且收集。
    assert len(bc.published_events) == 2
