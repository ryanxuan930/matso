"""修復與人員補充（WP-C7.3）：三個前提、遭襲中斷、前線不整補。

[JCATS-A p.26–27]：**「絕非申請後直接恢復戰力」**，且要「於後方恢復再前送」。
"""

from __future__ import annotations

from app.engine.refit_wiring import (
    PARTS_PER_POINT,
    REFIT_TICK_KEY,
    SAFE_DISTANCE_M,
    enemy_near,
    is_under_attack,
    refit_tick,
)
from app.engine.supply_points import SUPPLY_POINT_KIND
from app.engine.supply_wiring import SUPPLY_KEY
from app.engine.suppression_wiring import SUPPRESSION_KEY
from app.state.hot_state import InMemoryHotState

_LAT, _LNG = 24.0, 121.0
_TICK_MS = 60_000
_DAY = 1440


def _add_point(db, session_id: str):  # type: ignore[no-untyped-def]
    from app.models.tables import MapFeature

    db.add(
        MapFeature(
            session_id=session_id,
            kind=SUPPLY_POINT_KIND,
            geometry_type="POINT",
            geometry=[_LNG, _LAT],
            owner_faction="BLUE",
            influence_radius_m=0.0,
            attributes={"stock": {"IX": 100.0}},
        )
    )
    db.flush()


def _damaged(**extra):  # type: ignore[no-untyped-def]
    return {
        "lat": _LAT,
        "lng": _LNG,
        "strength": 60.0,
        "authorized_strength": 100.0,
        SUPPLY_KEY: {"IX": [20.0, 20.0]},
        **extra,
    }


def _lookup(_uid: str):  # type: ignore[no-untyped-def]
    return ("BLUE", _LAT, _LNG)


def _no_enemies(_uid: str) -> str:
    return "BLUE"


# ---- 中性：既有局零成本 ----


def test_zero_repair_rate_does_not_even_open_a_db_session() -> None:
    """`repair_per_day=0`（預設）→ **第一行就回空 list**，一次查詢都不做。"""

    class _ExplodingDb:
        def scalars(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise AssertionError("修復率為 0 時不該查任何東西")

    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged())
    assert refit_tick(_ExplodingDb(), hot, "s1", _lookup, _no_enemies, 1, _TICK_MS, 0.0) == []


def test_a_full_strength_unit_is_not_a_refit_candidate(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit("u1", {"strength": 100.0, "authorized_strength": 100.0})
    assert refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 1, _TICK_MS, 10.0) == []
    db.close()


# ---- 三個前提 ----


def test_refit_needs_a_supply_point(session_factory) -> None:  # type: ignore[no-untyped-def]
    """後方，不是隨便哪裡。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged())
    assert refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 1, _TICK_MS, 10.0) == []
    db.close()


def test_refit_needs_repair_parts(session_factory) -> None:  # type: ignore[no-untyped-def]
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged(**{SUPPLY_KEY: {"IX": [0.0, 20.0]}}))
    hot.update_unit("u1", {REFIT_TICK_KEY: 0})  # 曾在整補 → 受阻要發事件
    events = refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 1, _TICK_MS, 10.0)
    assert [e.ai_decision["reason"] for e in events] == ["NO_PARTS"]
    db.close()


def test_the_front_line_is_not_a_refit_area(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**[JCATS-A p.27] 「於後方恢復再前送」**——附近有敵軍就不整補。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged())
    hot.put_unit("enemy", {"lat": _LAT + 0.005, "lng": _LNG})  # 約 550 m
    hot.update_unit("u1", {REFIT_TICK_KEY: 0})

    def factions(uid: str) -> str:
        return "RED" if uid == "enemy" else "BLUE"

    events = refit_tick(db, hot, world.session_id, _lookup, factions, 1, _TICK_MS, 10.0)
    assert [e.ai_decision["reason"] for e in events] == ["ENEMY_NEAR"]
    db.close()


def test_enemy_near_uses_ground_truth_and_ignores_own_side() -> None:
    hot = InMemoryHotState()
    hot.put_unit("friend", {"lat": _LAT, "lng": _LNG})
    assert enemy_near(hot, lambda _u: "BLUE", "BLUE", _LAT, _LNG, SAFE_DISTANCE_M) is False
    hot.put_unit("foe", {"lat": _LAT, "lng": _LNG})
    factions = {"friend": "BLUE", "foe": "RED"}.get
    assert enemy_near(hot, factions, "BLUE", _LAT, _LNG, SAFE_DISTANCE_M) is True


# ---- 驗收條文：遭襲即中斷 ----


def test_being_fired_on_interrupts_refit(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**驗收條文**：修復中的單位遭襲即中斷整補。

    判定用**壓制度**——被射擊就會累積壓制（WP-C1），那是「遭襲」最直接而且已經存在的訊號。
    用「戰力下降」判會慢一拍（要真的被打掉人才算），而整補該在第一發子彈打過來時就停。
    """
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged())

    # 第一個 tick：開始整補（**只計時不修**）。
    started = refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 0, _TICK_MS, 10.0)
    assert [e.event_type for e in started] == ["REFIT_STARTED"]
    assert hot.get_unit("u1")["strength"] == 60.0, "第一個 tick 不該恢復任何戰力"

    # 挨了一發 → 中斷。
    hot.update_unit("u1", {SUPPRESSION_KEY: 0.4})
    blocked = refit_tick(db, hot, world.session_id, _lookup, _no_enemies, _DAY, _TICK_MS, 10.0)
    assert [e.ai_decision["reason"] for e in blocked] == ["UNDER_ATTACK"]
    assert hot.get_unit("u1")["strength"] == 60.0
    assert hot.get_unit("u1")[REFIT_TICK_KEY] is None, "中斷後計時要歸零——不能接著算"
    db.close()


def test_is_under_attack_reads_suppression() -> None:
    assert is_under_attack({}) is False
    assert is_under_attack({SUPPRESSION_KEY: 0.0}) is False
    assert is_under_attack({SUPPRESSION_KEY: 0.1}) is True


# ---- 修復本身 ----


def test_repair_takes_time_and_consumes_parts(session_factory) -> None:  # type: ignore[no-untyped-def]
    """「絕非申請後直接恢復戰力」——第一個 tick 只計時，之後按經過時間累積。"""
    from _order_fakes import seed_world

    from app.adjudication.supply import SupplyClass
    from app.engine.supply_wiring import read_levels

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged())

    refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 0, _TICK_MS, 10.0)
    events = refit_tick(db, hot, world.session_id, _lookup, _no_enemies, _DAY, _TICK_MS, 10.0)
    assert [e.event_type for e in events] == ["REFIT_PROGRESS"]
    state = hot.get_unit("u1")
    assert state["strength"] == 70.0  # 一天 10 點
    parts = read_levels(state)[SupplyClass.IX]
    assert parts.on_hand == 20.0 - 10.0 * PARTS_PER_POINT
    db.close()


def test_repair_never_exceeds_authorized_strength(session_factory) -> None:  # type: ignore[no-untyped-def]
    """整補不會把部隊修得比編制還強。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged(strength=99.0))
    refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 0, _TICK_MS, 999.0)
    refit_tick(db, hot, world.session_id, _lookup, _no_enemies, _DAY, _TICK_MS, 999.0)
    assert hot.get_unit("u1")["strength"] == 100.0
    db.close()


def test_parts_are_a_hard_ceiling_on_repair(session_factory) -> None:  # type: ignore[no-untyped-def]
    """有多少料修多少——料件不足時修復量被夾住，不會憑空生出戰力。"""
    from _order_fakes import seed_world

    world = seed_world(session_factory)
    db = session_factory()
    _add_point(db, world.session_id)
    db.commit()
    hot = InMemoryHotState()
    hot.put_unit("u1", _damaged(**{SUPPLY_KEY: {"IX": [1.0, 20.0]}}))
    refit_tick(db, hot, world.session_id, _lookup, _no_enemies, 0, _TICK_MS, 999.0)
    refit_tick(db, hot, world.session_id, _lookup, _no_enemies, _DAY, _TICK_MS, 999.0)
    # 1.0 料件 / 0.5 每點 = 最多修 2 點
    assert hot.get_unit("u1")["strength"] == 62.0
    db.close()
