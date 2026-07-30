"""盟軍也算觀測者（Backlog 清倉）。

SPEC 寫「任一**友軍**」、關係矩陣也讓盟軍互相可見，但兩處實作都只認自己陣營：
1. `c2/service.has_observer_on`（WP-C10.1 臨機火力申請的觀測條件）
2. `engine/fire_wiring.observer_verdict`（WP-C10.4a 的散布修正）

於是聯軍作戰時，盟軍的前觀明明看得到目標，本軍卻**叫不動火力**、或者**散布照樣加倍**。
與 WP-C9 的 `friendly_losses` 用字串比較是同一個 bug 家族：**敵我判斷不可以用 `==`**。
"""

from __future__ import annotations

from app.factions.relations import FactionRelations, Relation

_ALLIED = FactionRelations([("BLUE", "GREEN", Relation.ALLIED)])


class _AlwaysVisible:
    def has_los(self, *_a, **_k):  # type: ignore[no-untyped-def]
        class _O:
            visible = True
            clearance_m = 100.0

        return _O()


def _seed(db, factions):  # type: ignore[no-untyped-def]
    """在 (24.0, 121.0) 各放一個指定陣營的單位。"""
    from app.models.enums import UnitLevel
    from app.models.tables import TacticalUnit, WargameSession

    db.add(WargameSession(id="s1", name="x", master_seed=1, current_weather={}))
    db.flush()
    for i, faction in enumerate(factions):
        db.add(
            TacticalUnit(
                session_id="s1",
                designation=f"U{i}",
                unit_level=UnitLevel.PLATOON,
                faction=faction,
                current_lat=24.0,
                current_lng=121.0,
                authorized_strength=100.0,
                current_strength=100.0,
            )
        )
    db.commit()


# ---- has_observer_on（API 路徑）----


def test_an_allied_observer_now_counts(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**只有 GREEN 盟軍在場**——BLUE 應該叫得動火力。"""
    from app.c2.service import has_observer_on

    db = session_factory()
    _seed(db, ["GREEN"])
    assert (
        has_observer_on(db, "s1", "BLUE", (24.0, 121.0), _AlwaysVisible(), relations=_ALLIED)
        is True
    )
    db.close()


def test_a_hostile_unit_is_not_an_observer(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.c2.service import has_observer_on

    db = session_factory()
    _seed(db, ["RED"])
    assert (
        has_observer_on(db, "s1", "BLUE", (24.0, 121.0), _AlwaysVisible(), relations=_ALLIED)
        is False
    )
    db.close()


def test_without_relations_it_falls_back_to_own_faction_only(session_factory) -> None:  # type: ignore[no-untyped-def]
    """未注入 relations → 只認自己陣營（既有呼叫端不受影響）。"""
    from app.c2.service import has_observer_on

    db = session_factory()
    _seed(db, ["GREEN"])
    assert has_observer_on(db, "s1", "BLUE", (24.0, 121.0), _AlwaysVisible()) is False
    db.close()


def test_own_faction_still_counts(session_factory) -> None:  # type: ignore[no-untyped-def]
    from app.c2.service import has_observer_on

    db = session_factory()
    _seed(db, ["BLUE"])
    assert (
        has_observer_on(db, "s1", "BLUE", (24.0, 121.0), _AlwaysVisible(), relations=_ALLIED)
        is True
    )
    db.close()


def test_the_observer_faction_set_is_deterministically_ordered(session_factory) -> None:  # type: ignore[no-untyped-def]
    """觀測者陣營集會進 SQL 的 IN——順序不穩會讓查詢計畫與（未來的）快取行為漂。"""
    from app.c2.service import _observer_factions

    db = session_factory()
    _seed(db, ["GREEN", "RED", "BLUE"])
    assert _observer_factions(db, "s1", "BLUE", _ALLIED) == ["BLUE", "GREEN"]
    db.close()


# ---- observer_verdict（tick 路徑）----


def _area_target(faction: str):  # type: ignore[no-untyped-def]
    from app.adjudication.area_fire import AreaTarget

    return AreaTarget(
        unit_id=f"u-{faction}",
        faction=faction,
        lat=24.0,
        lng=121.0,
        armor_class="SOFT",
        current_strength=100.0,
        authorized_strength=100.0,
        platform_count=10,
    )


def _adjudicator(relations):  # type: ignore[no-untyped-def]
    from app.engine.fire_wiring import AreaFireAdjudicator
    from app.engine.rng import DeterministicRNG
    from app.state.hot_state import InMemoryHotState

    return AreaFireAdjudicator(
        None,  # type: ignore[arg-type]
        InMemoryHotState(),
        DeterministicRNG(1, "area_fire"),
        lambda _uid: [],
        gateway=_AlwaysVisible(),
        relations=relations,
    )


def test_an_allied_spotter_prevents_the_dispersion_penalty() -> None:
    """**只有盟軍看著落點**——散布不該加倍。"""
    from app.engine.fire_wiring import ObserverVerdict

    verdict = _adjudicator(_ALLIED).observer_verdict([_area_target("GREEN")], "BLUE", (24.0, 121.0))
    assert verdict is ObserverVerdict.OBSERVED


def test_an_enemy_unit_at_the_impact_point_is_not_a_spotter() -> None:
    from app.engine.fire_wiring import ObserverVerdict

    verdict = _adjudicator(_ALLIED).observer_verdict([_area_target("RED")], "BLUE", (24.0, 121.0))
    assert verdict is ObserverVerdict.UNOBSERVED


def test_without_relations_only_own_faction_spots() -> None:
    """既有呼叫端（未注入 relations）維持只認自己陣營。"""
    from app.engine.fire_wiring import ObserverVerdict

    adj = _adjudicator(None)
    assert (
        adj.observer_verdict([_area_target("GREEN")], "BLUE", (24.0, 121.0))
        is ObserverVerdict.UNOBSERVED
    )
    assert (
        adj.observer_verdict([_area_target("BLUE")], "BLUE", (24.0, 121.0))
        is ObserverVerdict.OBSERVED
    )
