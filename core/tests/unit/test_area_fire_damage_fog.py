"""面射擊的戰果不得免費回饋（WP-C10.4a）。

C10.2 自己在 worklog 裡記下這個洞：`AREA_FIRE_RESOLVED` 的 `damage_calc` 會被
`build_event_envelope` 原封不動帶進 WS 戰況 feed——**射方不需要任何觀測就立刻知道
打死了幾個**。間瞄火力打的是看不見的地方，那個回饋不該是免費的。

帳本上的 `damage_calc` 仍是真值（AAR 要真的）；這裡擋的只有「投影給玩家/AI」這條路。
"""

from __future__ import annotations

from app.state.broadcaster import build_event_envelope, feed_damage
from app.state.ledger import LedgerEvent


def _area_fire(damage: float = 42.5) -> LedgerEvent:
    return LedgerEvent(
        event_type="AREA_FIRE_RESOLVED",
        tick=9,
        initiator_id="ARTY",
        damage_calc=damage,
        ai_decision={"aim_lat": 24.0, "aim_lng": 121.0, "observation": "UNOBSERVED"},
    )


def test_area_fire_damage_is_not_projected() -> None:
    assert feed_damage("AREA_FIRE_RESOLVED", 42.5) is None


def test_direct_fire_damage_still_is() -> None:
    """直射不在此列：打得到就看得到。這是刻意的差別，不是漏掉。"""
    assert feed_damage("ENGAGEMENT_RESOLVED", 42.5) == 42.5


def test_the_envelope_has_no_damage_field_at_all() -> None:
    """**送 None 而不是 0**——0 會被讀成「打了但沒傷到」，那是另一個假情報。"""
    payload = build_event_envelope(_area_fire())["payload"]
    assert "damage" not in payload
    assert payload["event_type"] == "AREA_FIRE_RESOLVED"  # 事件本身照推，只是不帶數字


def test_the_ledger_event_itself_is_untouched() -> None:
    """帳本是真值來源——投影層擋住不代表事實被改掉了，AAR 仍要看得到真數字。"""
    event = _area_fire()
    build_event_envelope(event)
    assert event.damage_calc == 42.5


def test_ai_briefing_uses_the_same_rule() -> None:
    """**兩個投影邊界**：WS feed 與 AI briefing。

    只補其中一個的話，人看不到但 LLM 指揮官仍握有完美戰果評估——
    那種不對稱比全部洩漏更難察覺。
    """
    from app.ai_loop.world_view import _event_summary

    class _Row:
        tick = 9
        event_type = "AREA_FIRE_RESOLVED"
        damage_calc = 42.5

    out = _event_summary(_Row(), _area_fire())  # type: ignore[arg-type]
    assert "damage" not in out


def test_ai_briefing_still_reports_direct_fire_damage() -> None:
    from app.ai_loop.world_view import _event_summary

    class _Row:
        tick = 9
        event_type = "ENGAGEMENT_RESOLVED"
        damage_calc = 30.0

    event = LedgerEvent(event_type="ENGAGEMENT_RESOLVED", tick=9, damage_calc=30.0)
    out = _event_summary(_Row(), event)  # type: ignore[arg-type]
    assert out["damage"] == 30.0
