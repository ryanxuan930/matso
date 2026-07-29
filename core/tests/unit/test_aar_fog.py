"""AAR 的戰場迷霧（Backlog 清理）——**進行中的演習，AAR 不是敵情窗口**。

`GET /aar/*` 對任一參與者開放，而事件流原本是整包 `ai_decision` 直出。
C10.4 把 WS feed 與 AI briefing 兩條路上了迷霧，這條 REST 卻整個敞著——
少了它，那張卡宣稱的「沒有觀測就沒有戰果」是假的。
"""

from __future__ import annotations

from app.aar.events import AarEvent
from app.aar.fog import project_events
from app.aar.stats import compute_metrics

_FACTIONS = {"B1": "BLUE", "R1": "RED", "R2": "RED"}


def _area_fire(seq: int = 1) -> AarEvent:
    return AarEvent(
        seq=seq,
        tick=10,
        event_type="AREA_FIRE_RESOLVED",
        initiator_id="B1",
        target_id=None,
        ai_decision={
            "aim_lat": 24.0,
            "aim_lng": 121.0,
            "losses_by_unit": {"R1": 30.0, "R2": 12.0},
            "impacts": [[24.0, 121.0], [24.001, 121.001]],
            "friendly_losses": [],
        },
        damage_calc=42.0,
    )


def _red_engagement(seq: int = 2) -> AarEvent:
    return AarEvent(
        seq=seq,
        tick=11,
        event_type="ENGAGEMENT_RESOLVED",
        initiator_id="R1",
        target_id="R2",  # 純紅方內部事件（演訓/誤擊皆可），藍方不該看到
        ai_decision={"status": "HIT"},
        damage_calc=5.0,
    )


def _project(events: list[AarEvent], faction: str) -> list[AarEvent]:
    return project_events(events, faction=faction, omniscient=False, faction_for=_FACTIONS)


# ---- 受眾 ----


def test_an_enemy_only_event_is_not_visible() -> None:
    assert _project([_red_engagement()], "BLUE") == []


def test_your_own_event_is_visible() -> None:
    out = _project([_area_fire()], "BLUE")
    assert len(out) == 1


def test_omniscient_sees_everything_untouched() -> None:
    """統裁/白軍/ANALYST 本來就有權看全部——**一個位元都不動**。"""
    events = [_area_fire(), _red_engagement()]
    assert project_events(events, faction="", omniscient=True, faction_for=_FACTIONS) == events


# ---- 欄位 ----


def test_per_unit_losses_are_stripped_even_from_your_own_event() -> None:
    """**這條是本次修補的核心。**

    `losses_by_unit` 是敵軍逐單位的真實戰損——BDA 之所以要帶誤差，就是為了不給這個。
    自己打出去的砲也一樣：射方知道自己開了火，不代表知道打死了誰。
    """
    dec = _project([_area_fire()], "BLUE")[0].ai_decision
    assert "losses_by_unit" not in dec
    assert "aim_lat" in dec  # 自己瞄哪裡是自己的資訊，保留


def test_impact_points_are_stripped() -> None:
    """逐發落點可以拿來反推敵軍確切座標（比對落點與戰損）。"""
    assert "impacts" not in _project([_area_fire()], "BLUE")[0].ai_decision


def test_area_fire_damage_number_is_stripped() -> None:
    """與 WS feed 同一條規則（`feed_damage`）——不是另寫一套。"""
    assert _project([_area_fire()], "BLUE")[0].damage_calc is None


def test_direct_fire_damage_survives() -> None:
    """直射打得到就看得到，那是刻意的差別。"""
    e = AarEvent(
        seq=3,
        tick=12,
        event_type="ENGAGEMENT_RESOLVED",
        initiator_id="B1",
        target_id="R1",
        ai_decision={"status": "HIT"},
        damage_calc=8.0,
    )
    assert _project([e], "BLUE")[0].damage_calc == 8.0


# ---- 統計歸帳 ----


def test_area_fire_losses_are_attributed_to_the_victim_faction() -> None:
    """面射擊沒有單一 `target_id`，原本整個歸不了帳——砲兵戰損在 AAR 上會憑空消失。

    `losses_by_unit` 早就寫在事件裡，只是從來沒有人讀。
    """
    m = compute_metrics([_area_fire()], _FACTIONS)
    assert m.damage_by_faction["RED"] == 42.0  # 30 + 12


def test_stats_still_attribute_direct_fire_by_target_id() -> None:
    m = compute_metrics([_red_engagement()], _FACTIONS)
    assert m.damage_by_faction["RED"] == 5.0
