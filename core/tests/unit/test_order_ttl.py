"""指令時效（WP-C10.3）——**過期的準備射擊不該遲到幾十個 tick 才落地**。

執行期的通信閘門遇到射手 OFFLINE 的做法是「留在 VALIDATED，等通聯恢復再打」。
對大多數令這是對的；對**時效性火力**不是：射手斷聯 40 個 tick 之後才把彈打出去，
打的是 40 個 tick 前的戰場。真實作業裡這種任務會作廢、由火協重新指派。
"""

from __future__ import annotations

from app.orders.ttl import expired, ttl_of


def test_missing_ttl_never_expires() -> None:
    """**中性預設**：沒宣告時效 → 永不過期，行為與過去逐字相同。

    既有想定、既有 session、以及所有沒有時效需求的令都靠這條。
    """
    for payload in ({}, None, {"ttl_ticks": None}, {"ttl_ticks": 0}, {"ttl_ticks": -5}, "壞資料"):
        assert ttl_of(payload) == 0
        assert expired(payload, issued_tick=0, now_tick=10_000) is False


def test_expiry_is_measured_from_when_the_order_was_issued() -> None:
    """以發令 tick 起算，不是排定 tick。

    `issued_at_tick` 是每個 Order 都有的欄位；用 `at_tick` 的話只有火力計畫那條路徑
    有得比，其他令型就用不了同一個機制。
    """
    p = {"ttl_ticks": 5}
    assert expired(p, issued_tick=100, now_tick=104) is False  # 還在時效內
    assert expired(p, issued_tick=100, now_tick=105) is False  # 剛好第 5 個 tick，準時
    assert expired(p, issued_tick=100, now_tick=106) is True  # 第 6 個 tick 才算遲到


def test_ttl_one_means_valid_for_one_tick() -> None:
    """`ttl_ticks=1` ＝「發令後 1 個 tick 內仍有效」。

    用 `>=` 比較的話 ttl=1 會在發令的下一個 tick 就過期，實際上等於 0——
    這是 off-by-one 最容易發生的地方。
    """
    p = {"ttl_ticks": 1}
    assert expired(p, issued_tick=10, now_tick=11) is False
    assert expired(p, issued_tick=10, now_tick=12) is True


def test_an_expired_fire_mission_is_rejected_not_silently_dropped() -> None:
    """逾時的火力任務要走 `_reject`——**落帳**，不是靜靜消失。

    「叫了火力但沒打出去」是要能追究的事；靜靜作廢的話，AAR 上看起來像從沒下過令。
    """
    from types import SimpleNamespace

    from app.engine.clock import SimTime
    from app.engine.fire_wiring import AreaFireAdjudicator, FireMissionCommand

    adj = AreaFireAdjudicator.__new__(AreaFireAdjudicator)
    adj._hot = SimpleNamespace(get_unit=lambda _uid: {"lat": 24.0, "lng": 121.0})  # type: ignore[attr-defined]
    adj._db = None  # type: ignore[attr-defined]

    completed: list[str] = []
    adj._complete = lambda oid, _t: completed.append(oid)  # type: ignore[attr-defined,assignment]

    order = FireMissionCommand(
        order_id="o1",
        shooter_id="u1",
        target_lat=24.0,
        target_lng=121.0,
        issued_at_tick=10,
        ttl_ticks=3,
    )
    events = adj.resolve(order, SimTime(tick=100, sim_time_ms=0))
    assert events, "逾時作廢仍必須落帳"
    payload = events[0].ai_decision or {}
    assert payload.get("status") == "REJECTED"
    assert payload.get("reason") == "EXPIRED"
    assert "逾時作廢" in str(payload.get("reason_detail"))
