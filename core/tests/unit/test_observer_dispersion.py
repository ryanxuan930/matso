"""觀測判定與散布加倍（WP-C10.4a）——「有沒有人在看」要有後果。

驗收條件（SPEC_V2 §WP-C10）：**前觀死亡後 on-call 任務失去觀測修正，散布加倍**。
"""

from __future__ import annotations

import math

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.adjudication.area_fire import AreaTarget, resolve_area_fire
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimTime
from app.engine.fire_wiring import (
    NO_OBSERVER_DISPERSION_MULT,
    AreaFireAdjudicator,
    FireMissionCommand,
    ObserverVerdict,
    dispersion_multiplier,
)
from app.engine.rng import DeterministicRNG
from app.models import Base
from app.orders.precheck import LosOutcome
from app.state.hot_state import InMemoryHotState

_AIM = (24.0, 121.0)
_NOW = SimTime(tick=7, sim_time_ms=7000)


def _weapon(cep: float = 100.0, lethal: float = 50.0) -> WeaponProfile:
    return WeaponProfile.from_base_stats(
        {
            "max_range_m": 25000,
            "ph_by_range_band": [[25000, 0.5]],
            "damage_by_armor_class": {"SOFT": 60.0},
            "pk_by_armor_class": {"SOFT": 0.6},
            "ammo_types": ["HE"],
            "dispersion_cep_m": cep,
            "lethal_radius_m": lethal,
        }
    )


def _rng(stream: str = "area_fire") -> DeterministicRNG:
    return DeterministicRNG(master_seed=11, stream_id=stream)


class _Gateway:
    """可設定的假 LOS gateway，並記錄探測次數。"""

    def __init__(self, visible: bool = True, raises: bool = False) -> None:
        self.visible, self.raises = visible, raises
        self.calls = 0

    def has_los(self, observer: object, target: object) -> LosOutcome:
        self.calls += 1
        if self.raises:
            raise RuntimeError("terrain down")
        return LosOutcome(self.visible, 12.0)


# ---- 倍率表 ----


def test_only_unobserved_doubles_dispersion() -> None:
    """**`UNKNOWN` 走 1.0（fail open）**：地形服務掛掉不該讓全場砲兵默默變不準。

    把「系統答不出來」演成「戰術上沒人看得到」是最難查的一種錯——現象是精度下降，
    原因卻在基礎設施。
    """
    assert dispersion_multiplier(ObserverVerdict.OBSERVED) == 1.0
    assert dispersion_multiplier(ObserverVerdict.UNOBSERVED) == NO_OBSERVER_DISPERSION_MULT
    assert dispersion_multiplier(ObserverVerdict.UNKNOWN) == 1.0


# ---- 純函數的倍率 ----


def test_multiplier_one_is_bit_identical() -> None:
    """`1.0` **必須位元不變**——否則既有局的落點序列全部偏移，golden 也會動。"""
    w = _weapon()
    a = resolve_area_fire(w, _AIM, [], _rng(), 5, shooter_id="B1", rounds=3)
    b = resolve_area_fire(w, _AIM, [], _rng(), 5, shooter_id="B1", rounds=3, dispersion_mult=1.0)
    assert a.event.ai_decision["impacts"] == b.event.ai_decision["impacts"]  # type: ignore[union-attr]


def test_doubling_actually_spreads_the_impacts() -> None:
    """加倍要真的變散——不是只把一個數字記在事件裡。"""
    w = _weapon(cep=200.0)

    def mean_offset(mult: float) -> float:
        out = resolve_area_fire(
            w, _AIM, [], _rng(), 5, shooter_id="B1", rounds=40, dispersion_mult=mult
        )
        pts = out.event.ai_decision["impacts"]  # type: ignore[union-attr]
        return sum(math.hypot(p[0] - _AIM[0], p[1] - _AIM[1]) for p in pts) / len(pts)

    tight, loose = mean_offset(1.0), mean_offset(2.0)
    assert loose > tight * 1.5, f"加倍後平均偏移只從 {tight} 變成 {loose}"


def test_zero_cep_stays_a_point_hit_even_when_doubled() -> None:
    """沒有散布資料的武器乘以 2 還是 0——早退路徑（不抽樣）必須維持。"""
    w = _weapon(cep=0.0)
    out = resolve_area_fire(w, _AIM, [], _rng(), 5, shooter_id="B1", dispersion_mult=2.0)
    assert (out.impact_lat, out.impact_lng) == _AIM


# ---- 觀測判定 ----


def _adj(gateway: object | None) -> AreaFireAdjudicator:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    return AreaFireAdjudicator(
        factory(),
        InMemoryHotState(),
        _rng(),
        lambda _uid: [],
        faction_for=lambda uid: "BLUE",
        gateway=gateway,
    )


def _t(
    uid: str, faction: str, lat: float, lng: float, strength: float | None = 100.0
) -> AreaTarget:
    return AreaTarget(
        unit_id=uid,
        faction=faction,
        lat=lat,
        lng=lng,
        armor_class="SOFT",
        current_strength=strength,
        authorized_strength=100.0,
    )


def test_a_living_friendly_with_los_is_an_observer() -> None:
    adj = _adj(_Gateway(visible=True))
    verdict = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.OBSERVED


def test_a_dead_friendly_is_not_an_observer() -> None:
    """**這條就是驗收條件的核心**：前觀死了就沒有觀測修正。

    死亡要看熱狀態的戰力——DB 的座標欄位在單位死後照樣留著，且沒有存活旗標。
    """
    adj = _adj(_Gateway(visible=True))
    verdict = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0, strength=0.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNOBSERVED


def test_enemy_units_are_not_our_observers() -> None:
    adj = _adj(_Gateway(visible=True))
    verdict = adj.observer_verdict([_t("R1", "RED", 24.001, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNOBSERVED


def test_a_friendly_too_far_away_is_not_an_observer() -> None:
    """只看 LOS 不看距離的話，40 km 外的單位會被當成前觀。"""
    adj = _adj(_Gateway(visible=True))
    verdict = adj.observer_verdict([_t("FAR", "BLUE", 25.5, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNOBSERVED


def test_blocked_line_of_sight_means_unobserved() -> None:
    adj = _adj(_Gateway(visible=False))
    verdict = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNOBSERVED


def test_no_gateway_is_unknown_not_unobserved() -> None:
    adj = _adj(None)
    verdict = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNKNOWN


def test_terrain_failure_is_unknown_and_never_escapes() -> None:
    """地形服務掛掉 → UNKNOWN，**且不得往上拋**。

    `kernel.run_tick` 與 `run_paced` 對裁決都沒有防護，例外會讓 runner 崩潰、
    3 秒後被 SimManager 重建——在服務中斷期間變成重啟迴圈。
    """
    adj = _adj(_Gateway(raises=True))
    verdict = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0)], "BLUE", _AIM)
    assert verdict is ObserverVerdict.UNKNOWN


def test_probe_count_is_capped() -> None:
    """tick 預算 200ms，每次 LOS 是一趟 gRPC——不設上限的話一個大編組就吃光整個 tick。"""
    gw = _Gateway(visible=False)
    adj = _adj(gw)
    many = [_t(f"U{i:03d}", "BLUE", 24.0 + i * 0.0001, 121.0) for i in range(60)]
    adj.observer_verdict(many, "BLUE", _AIM)
    assert gw.calls <= 8, f"探了 {gw.calls} 次 LOS"


def test_nearest_units_are_probed_first() -> None:
    """截斷前先依距離排序：真正看得到落點的本來就是最近的那幾個。"""
    gw = _Gateway(visible=True)
    adj = _adj(gw)
    far = [_t(f"F{i}", "BLUE", 24.05 + i * 0.001, 121.0) for i in range(20)]
    near = _t("NEAR", "BLUE", 24.0005, 121.0)
    assert adj.observer_verdict([*far, near], "BLUE", _AIM) is ObserverVerdict.OBSERVED
    assert gw.calls == 1, "最近的那個沒有被排在最前面"


def test_no_shooter_faction_is_unknown() -> None:
    """陣營解析不出來（局中新增的單位）→ 判不出觀測，不亂猜。"""
    adj = _adj(_Gateway(visible=True))
    out = adj.observer_verdict([_t("FO", "BLUE", 24.01, 121.0)], "", _AIM)
    assert out is ObserverVerdict.UNKNOWN


# ---- 端到端：令 → 裁決 → 事件上的觀測欄位 ----


def _fire_with(gateway: object | None, fo_alive: bool) -> dict[str, object]:
    """一門砲 + 一個前觀（生／死）→ 回 AREA_FIRE_RESOLVED 的 ai_decision。"""
    return _fire_events(gateway, fo_alive)[0]


def _fire_events(gateway: object | None, fo_alive: bool) -> list[dict[str, object]]:
    """同上，但回**全部**事件的 ai_decision（含 BDA_REPORT，若有）。"""
    from app.engine.engage_wiring import WeaponEntry
    from app.models.enums import OrderStatus, UnitLevel
    from app.models.tables import Order, TacticalUnit, WargameSession

    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    db: Session = factory()
    s = WargameSession(name="w", master_seed=3, current_weather={})
    db.add(s)
    db.flush()
    gun = TacticalUnit(
        session_id=s.id,
        designation="ARTY",
        unit_level=UnitLevel.PLATOON,
        faction="BLUE",
        current_lat=24.0,
        current_lng=120.82,
    )
    db.add(gun)
    db.flush()
    order = Order(
        session_id=s.id,
        issuer_id="u",
        unit_id=gun.id,
        order_type="FIRE_MISSION",
        payload={},
        status=OrderStatus.EXECUTING,
        issued_at_tick=1,
    )
    db.add(order)
    db.commit()

    hot = InMemoryHotState()
    hot.put_unit(
        gun.id,
        {
            "lat": 24.0,
            "lng": 120.82,
            "ammo_by_weapon": {"w1": 30},
            "strength": 100.0,
            "authorized_strength": 100.0,
        },
    )
    hot.put_unit(
        "FO",
        {
            "lat": 24.005,
            "lng": 121.0,
            "strength": 100.0 if fo_alive else 0.0,
            "authorized_strength": 100.0,
        },
    )
    entry = WeaponEntry(
        weapon_id="w1", profile=_weapon(cep=150.0), quantity=1, ammo=30, category="ARTILLERY"
    )
    adj = AreaFireAdjudicator(
        db,
        hot,
        _rng(),
        lambda _uid: [entry],
        faction_for=lambda _uid: "BLUE",
        gateway=gateway,
        bda_rng=_rng("bda"),
    )
    events = adj.resolve(FireMissionCommand(order.id, gun.id, _AIM[0], _AIM[1], rounds=2), _NOW)
    assert events and events[0].ai_decision is not None
    return [dict(e.ai_decision or {}) | {"_type": e.event_type} for e in events]


def test_a_gun_that_can_see_its_own_impact_is_its_own_observer() -> None:
    """**刻意不把射手排除在觀測者之外。**

    砲能直接看到落點就是直射（direct lay），它當然看得見自己的彈著。
    真正的間瞄是打看不見的地方——那種情況下砲離落點遠或被地形擋住，
    自然就不會成為候選（本檔的端到端測試把砲放在 18 km 外，超出觀測距離）。
    """
    adj = _adj(_Gateway(visible=True))
    gun = _t("ARTY", "BLUE", 24.002, 121.0)
    assert adj.observer_verdict([gun], "BLUE", _AIM) is ObserverVerdict.OBSERVED


def test_live_fo_keeps_normal_dispersion() -> None:
    dec = _fire_with(_Gateway(visible=True), fo_alive=True)
    assert dec["observation"] == "OBSERVED"
    assert dec["dispersion_mult"] == 1.0


def test_dead_fo_doubles_dispersion_end_to_end() -> None:
    """**SPEC_V2 的驗收條件**：前觀死亡後失去觀測修正，散布加倍。"""
    dec = _fire_with(_Gateway(visible=True), fo_alive=False)
    assert dec["observation"] == "UNOBSERVED"
    assert dec["dispersion_mult"] == NO_OBSERVER_DISPERSION_MULT


def test_observation_is_recorded_in_the_ledger() -> None:
    """gateway 的答覆是**外部狀態**，重播重建不出來——不落帳就無從稽核「為什麼那次散布加倍」。"""
    dec = _fire_with(None, fo_alive=True)
    assert dec["observation"] == "UNKNOWN"
    assert dec["dispersion_mult"] == 1.0


# ---- WP-C10.4b：沒有觀測就沒有戰果評估 ----


def test_an_observed_mission_produces_a_bda_report() -> None:
    types = [d["_type"] for d in _fire_events(_Gateway(visible=True), fo_alive=True)]
    assert types == ["AREA_FIRE_RESOLVED", "BDA_REPORT"]


def test_an_unobserved_mission_produces_no_bda_at_all() -> None:
    """**不是回報 0**——0 會被讀成「打了但沒傷到」，是另一種假情報。

    什麼都不發，射方就只知道「砲打出去了」，那正是他沒有前觀時實際擁有的資訊。
    """
    types = [d["_type"] for d in _fire_events(_Gateway(visible=True), fo_alive=False)]
    assert types == ["AREA_FIRE_RESOLVED"]


def test_unknown_observation_also_produces_no_bda() -> None:
    """判不出來時不加倍散布（fail open），但**也不憑空生一份戰果評估**。"""
    types = [d["_type"] for d in _fire_events(None, fo_alive=True)]
    assert types == ["AREA_FIRE_RESOLVED"]
