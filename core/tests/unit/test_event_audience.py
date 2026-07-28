"""交戰/偵測事件的受眾標籤（fog of war）。

原本 Kernel 發出的事件**完全沒有受眾標籤** → `is_visible` 一律放行 → 每個陣營都收得到
他方的交戰與偵測事件。本檔釘住修正後的規則，尤其是 SENSOR_CONTACT 那條陷阱。
"""

from __future__ import annotations

import pytest

from app.state.broadcaster import build_event_envelope, event_audience
from app.state.ledger import LedgerEvent
from app.stream.faction_filter import is_visible

_FACTIONS = {"b1": "BLUE", "r1": "RED", "y1": "YELLOW"}


def _faction_for(unit_id: str) -> str:
    return _FACTIONS.get(unit_id, "")


def _engagement() -> LedgerEvent:
    return LedgerEvent(
        event_type="ENGAGEMENT_RESOLVED",
        tick=5,
        initiator_id="b1",
        target_id="r1",
        damage_calc=10.0,
        ai_decision={"status": "HIT"},
    )


def test_engagement_audience_is_both_sides() -> None:
    """射手與目標兩方都該知道這一槍；第三方不該。"""
    assert event_audience(_engagement(), _faction_for) == ["BLUE", "RED"]


def test_sensor_contact_goes_only_to_the_observer() -> None:
    """**陷阱**：SENSOR_CONTACT 的 target_id 是「被偵測到的單位」。

    若照 unit 推導受眾，等於通知對方「你被發現了」——這比原本的全廣播更糟。
    observer_faction 必須優先且為唯一受眾。
    """
    event = LedgerEvent(
        event_type="SENSOR_CONTACT",
        tick=5,
        target_id="r1",  # 被 BLUE 偵測到的 RED 單位
        ai_decision={"observer_faction": "BLUE", "fidelity": "DETECTED"},
    )

    audience = event_audience(event, _faction_for)

    assert audience == ["BLUE"]
    assert "RED" not in (audience or [])  # 被偵測方不得收到


def test_global_events_have_no_audience() -> None:
    """無所涉單位的全域事件（收場/關係變更）→ 不標受眾 ＝ 全體可見。"""
    event = LedgerEvent(event_type="SESSION_CONCLUDED", tick=9, ai_decision={"winners": ["BLUE"]})

    assert event_audience(event, _faction_for) is None


def test_no_lookup_keeps_legacy_broadcast_behaviour() -> None:
    """未注入 faction_for（測試/合成想定）→ 不標受眾，行為與加此功能前完全相同。"""
    assert event_audience(_engagement(), None) is None
    assert "factions" not in build_event_envelope(_engagement())


def test_envelope_carries_audience_and_filter_honours_it() -> None:
    env = build_event_envelope(_engagement(), _faction_for)

    assert env["factions"] == ["BLUE", "RED"]
    assert is_visible(env, "BLUE", omniscient=False)
    assert is_visible(env, "RED", omniscient=False)
    assert not is_visible(env, "YELLOW", omniscient=False)  # 第三方看不到
    assert is_visible(env, "YELLOW", omniscient=True)  # 白軍全知照收


@pytest.mark.parametrize("faction", ["BLUE", "RED", "YELLOW"])
def test_untagged_envelope_still_visible_to_all(faction: str) -> None:
    """既有無標籤 envelope（如 API 端全域公告）不受影響。"""
    assert is_visible({"v": 1, "type": "EVENT", "payload": {}}, faction, omniscient=False)
