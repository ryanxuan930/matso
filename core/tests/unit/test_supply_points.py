"""補給點與補給線（WP-C7.2）：拉式撥交、斷補給線、打擊敵後勤。

規格的一句話定義了這張卡的價值：**「這讓『打擊敵後勤』成為可行戰法」**。
"""

from __future__ import annotations

from app.adjudication.supply import SupplyClass, SupplyLevel
from app.engine.supply_points import (
    DRAW_RADIUS_M,
    SUPPLY_POINT_KIND,
    SupplyPoint,
    destroy_at,
    draw_from,
    load_points,
    nearest_usable,
    topped_up,
)
from app.engine.supply_wiring import SUPPLY_KEY, auto_resupply, read_levels
from app.state.hot_state import InMemoryHotState

_LAT, _LNG = 24.0, 121.0


def _point(**kw):  # type: ignore[no-untyped-def]
    base = {
        "feature_id": "p1",
        "faction": "BLUE",
        "lat": _LAT,
        "lng": _LNG,
        "stock": {SupplyClass.I: 100.0},
    }
    return SupplyPoint(**{**base, **kw})


def _add_point(db, session_id: str, *, faction="BLUE", lat=_LAT, lng=_LNG, stock=None, dead=False):  # type: ignore[no-untyped-def]
    from app.models.tables import MapFeature

    row = MapFeature(
        session_id=session_id,
        kind=SUPPLY_POINT_KIND,
        geometry_type="POINT",
        geometry=[lng, lat],
        owner_faction=faction,
        label="補給點",
        influence_radius_m=0.0,
        attributes={"stock": stock or {"I": 100.0}, **({"destroyed": True} if dead else {})},
    )
    db.add(row)
    db.flush()
    return row


# ---- 選點 ----


def test_a_destroyed_or_empty_point_is_not_usable() -> None:
    assert _point().usable is True
    assert _point(destroyed=True).usable is False
    assert _point(stock={SupplyClass.I: 0.0}).usable is False


def test_only_own_faction_points_are_drawn_from() -> None:
    """盟軍補給點要不要共用是**後勤協定問題不是物理問題**——預設不共用比較保守，
    要開放應該是想定的明確宣告而不是預設。"""
    points = [_point(faction="GREEN")]
    assert nearest_usable(points, "BLUE", _LAT, _LNG) is None
    assert nearest_usable(points, "GREEN", _LAT, _LNG) is not None


def test_a_point_out_of_range_is_not_reachable() -> None:
    far = _point(lat=_LAT + 1.0)  # 約 111 km
    assert nearest_usable([far], "BLUE", _LAT, _LNG) is None
    assert DRAW_RADIUS_M > 0


def test_the_nearest_usable_point_wins() -> None:
    near = _point(feature_id="near", lat=_LAT + 0.001)
    far = _point(feature_id="far", lat=_LAT + 0.02)
    assert nearest_usable([far, near], "BLUE", _LAT, _LNG).feature_id == "near"


# ---- 撥交 ----


def test_partial_issue_when_stock_runs_short(session_factory) -> None:  # type: ignore[no-untyped-def]
    """庫存不足時給一部分而不是整批拒絕——那才是真實的補給點行為，
    而且「拉到一半」正是指揮官需要看見的訊號（這個補給點快空了）。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    row = _add_point(db, world.session_id, stock={"I": 5.0})
    db.commit()
    point = load_points(db, world.session_id)[0]
    issued = draw_from(db, point, {SupplyClass.I: 50.0})
    db.commit()
    assert issued == {SupplyClass.I: 5.0}
    db.refresh(row)
    assert row.attributes["stock"]["I"] == 0.0
    db.close()


def test_a_destroyed_point_issues_nothing(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id, dead=True)
    db.commit()
    point = load_points(db, world.session_id)[0]
    assert draw_from(db, point, {SupplyClass.I: 10.0}) == {}
    db.close()


def test_topping_up_is_clamped_to_capacity() -> None:
    """背包裝不下就是裝不下。"""
    current = {SupplyClass.I: SupplyLevel(2.0, 10.0)}
    assert topped_up(current, {SupplyClass.I: 50.0})[SupplyClass.I].on_hand == 10.0


def test_topping_up_ignores_undeclared_classes() -> None:
    current = {SupplyClass.IX: SupplyLevel(0.0, 0.0)}
    assert topped_up(current, {SupplyClass.IX: 5.0})[SupplyClass.IX].on_hand == 0.0


# ---- 打擊敵後勤 ----


def test_destroying_a_point_cuts_the_supply_line(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**驗收條文**：打掉補給點後下游單位水位不再回升。"""
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    unit = db.get(TacticalUnit, world.blue_unit_id)
    unit.current_lat, unit.current_lng = _LAT, _LNG
    db.commit()

    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {SUPPLY_KEY: {"I": [1.0, 10.0]}})  # 低於水位

    def lookup(uid: str):  # type: ignore[no-untyped-def]
        return ("BLUE", _LAT, _LNG)

    events = auto_resupply(db, hot, world.session_id, lookup, tick=1)
    assert [e.event_type for e in events] == ["RESUPPLIED"]
    assert read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand == 10.0

    # 打掉補給點 → 再也拉不到。
    hot.update_unit(world.blue_unit_id, {SUPPLY_KEY: {"I": [1.0, 10.0]}})
    assert destroy_at(db, world.session_id, _LAT, _LNG, 500.0)
    db.commit()
    assert auto_resupply(db, hot, world.session_id, lookup, tick=2) == []
    assert read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand == 1.0
    db.close()


def test_a_supply_point_cannot_issue_more_than_it_holds(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**帳目守恆**：同一 tick 內多個單位來拉，撥出去的總量不得超過庫存。

    `auto_resupply` 在迴圈**外**載入 `points`，迴圈內每個單位都拿**同一個 `SupplyPoint`
    物件**去 `draw_from`。而 `draw_from` 只把扣減後的庫存寫進 `row.attributes`（DB），
    從不更新記憶體裡那份 `point.stock`——於是第二、第三個單位看到的還是**原始庫存**。

    後果不是「多給了一點」而是**這張卡的價值歸零**：規格說補給點的意義是
    「讓『打擊敵後勤』成為可行戰法」，而如果庫存不是真的約束，打掉它就不痛不癢。
    庫存 10 份的補給點可以無限量供應，只要來拉的單位夠多。

    測 `auto_resupply` 而不是 `draw_from`：`draw_from` 單獨看是對的（它照著收到的
    `point.stock` 算），洞長在「呼叫端重複使用同一個物件」這個互動上。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    row = _add_point(db, world.session_id, stock={"I": 10.0})
    db.commit()

    hot = InMemoryHotState()
    hungry = ["u-1", "u-2", "u-3"]
    for uid in hungry:
        hot.put_unit(uid, {SUPPLY_KEY: {"I": [0.0, 10.0]}})  # 各自想要 10 份

    events = auto_resupply(db, hot, world.session_id, lambda _u: ("BLUE", _LAT, _LNG), tick=1)

    issued_total = sum(
        sum(e.ai_decision["issued"].values()) for e in events if e.ai_decision is not None
    )
    received_total = sum(read_levels(hot.get_unit(uid))[SupplyClass.I].on_hand for uid in hungry)
    db.refresh(row)
    stock_after = float(row.attributes["stock"]["I"])

    assert issued_total == 10.0, f"補給點只有 10 份，卻撥出 {issued_total} 份"
    assert received_total == 10.0, f"單位總共收到 {received_total} 份，憑空多出來"
    assert stock_after == 0.0
    db.close()


def test_a_point_outside_the_blast_survives(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id, lat=_LAT + 0.05)  # 約 5.5 km 外
    db.commit()
    assert destroy_at(db, world.session_id, _LAT, _LNG, 500.0) == []
    db.close()


# ---- 中性：既有局什麼都不做 ----


def test_a_session_with_no_supply_points_does_nothing(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit(world.blue_unit_id, {SUPPLY_KEY: {"I": [1.0, 10.0]}})
    assert auto_resupply(db, hot, world.session_id, lambda _u: ("BLUE", _LAT, _LNG), 1) == []
    db.close()


def test_no_hungry_units_means_not_even_a_db_query() -> None:
    """既有局的單位沒有 `supply` 鍵 → 不會被列進補給名單 → **連補給點查詢都不發生**。

    ⚠ 只斷言「沒有事件」殺不掉「拿掉這個 early return」的突變——那條路徑下
    `hungry` 是空的，迴圈本來就不會跑。要真的驗到「零成本」就得讓 DB 一被碰就爆。
    （突變測試抓出來的。）
    """

    class _ExplodingDb:
        def scalars(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise AssertionError("沒有單位缺補時不該查補給點")

        def get(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise AssertionError("沒有單位缺補時不該查任何東西")

    hot = InMemoryHotState()
    hot.put_unit("u1", {"lat": _LAT, "lng": _LNG})  # 既有局：沒有 supply 鍵
    assert auto_resupply(_ExplodingDb(), hot, "s1", lambda _u: ("BLUE", _LAT, _LNG), 1) == []
