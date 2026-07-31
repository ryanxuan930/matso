"""後勤校準與整補（WP-C7 收尾）：把三個恆為 0 的常數變成真的數字之後，**鏈真的通了嗎**。

C7.1/C7.2/C7.3 三張卡的既有測試全綠，是因為它們自己 `put_unit` 了 `supply`
——繞過了真正缺的那一層。本檔的紀律因此只有一條：**每一條都走生產接線**。

- 播種走 `engine.engage_wiring.seed_combat_state`（熱狀態鍵集的單一寫入路徑），
  資料來源是**真的 `TacticalUnit.attributes`**（想定 loader 寫的就是這個形狀）。
- 每 tick 的結算走 `sim_runtime._supply_tick` / `_resupply_tick` / `_refit_tick`
  ——那是 tick loop 真正呼叫的那三個函式，不是它們底下的純函數。
- 交戰效能走真的 `EngagementAdjudicator`，斷言的是**目標實際掉了多少戰力**。
"""

from __future__ import annotations

import pytest
from _order_fakes import FakeGateway, seed_world
from sqlalchemy.orm import Session, sessionmaker

from app.adjudication.adjudicator import EngagementAdjudicator, EngageOrderSource
from app.adjudication.engagement import EnvSnapshot
from app.adjudication.supply import (
    DAILY_CONSUMPTION,
    SupplyClass,
    SupplyLevel,
    daily_consumption,
    parse_class,
)
from app.adjudication.weapon import WeaponProfile
from app.engine.clock import SimTime
from app.engine.engage_wiring import WeaponResolver, seed_combat_state
from app.engine.refit_wiring import PARTS_PER_POINT, REFIT_TICK_KEY, REPAIR_PER_DAY
from app.engine.rng import DeterministicRNG
from app.engine.supply_points import SUPPLY_POINT_KIND, destroy_at
from app.engine.supply_wiring import (
    STARVED_DAYS_KEY,
    SUPPLY_KEY,
    read_levels,
    supply_effectiveness,
    write_levels,
)
from app.models.tables import MapFeature, TacticalUnit
from app.orders.schemas import OrderRequest, OrderType
from app.orders.service import OrderService
from app.sim_runtime import _refit_tick, _resupply_tick, _supply_tick
from app.state.checkpoint import compute_state_hash
from app.state.hot_state import InMemoryHotState

_MIN = 60_000  # 1 tick = 1 分鐘（`_DAY` 個 tick ＝ 1 模擬日）
_SEC = 1_000  # 1 tick = 1 秒——**官方 demo 與使用者的想定寫的都是這個**
_DAY = 86_400_000 // _MIN

# 射程夠遠、命中率固定 1.0 的武器：交戰結果的唯一變數就是射手效能，
# 於是「掉了多少戰力」直接讀作「效能差多少」。
_WEAPON_STATS = {
    "max_range_m": 5000,
    "ph_by_range_band": [[100, 1.0], [5000, 1.0]],
    "damage_by_armor_class": {"INFANTRY": 40},
    "pk_by_armor_class": {"INFANTRY": 0.2},
    "ammo_types": ["X"],
    "rate_per_tick": 1,
}
_WEAPON = WeaponProfile.from_base_stats(_WEAPON_STATS)


def _declare(db: Session, unit_id: str, **levels: SupplyLevel) -> None:
    """把補給編制寫進**真的 `TacticalUnit.attributes`**——形狀與想定 loader 產出的一致
    （`scenario/loader._unit_attributes` 用的就是 `write_levels`）。

    手捲一份熱狀態 dict 是這張卡要消滅的東西：那樣測的是測試自己餵的資料，
    不是生產接線。連類別排序都交給 `write_levels`，因為那個排序正是雜湊穩定的來源。
    """
    unit = db.get(TacticalUnit, unit_id)
    assert unit is not None
    unit.attributes = {
        **(unit.attributes or {}),
        SUPPLY_KEY: write_levels({SupplyClass(c): lv for c, lv in levels.items()}),
    }
    db.commit()


def _arm(db: Session, unit_id: str, ammo: int) -> None:
    """配一件真的裝備——彈藥要由 `WeaponResolver.primary_ammo` 從 DB 讀出來，
    不是往熱狀態塞一個 `ammo` 數字（那又是「測試自己餵的資料」）。"""
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    template = EquipmentTemplate(name="RIFLE", category="KINETIC", base_stats=_WEAPON_STATS)
    db.add(template)
    db.flush()
    db.add(
        EquipmentInstance(template_id=template.id, owner_id=unit_id, current_state={"ammo": ammo})
    )
    db.commit()


def _seed(db: Session, session_id: str, hot: InMemoryHotState) -> int:
    """生產播種路徑。**不是** `hot.put_unit`。"""
    return seed_combat_state(db, hot, session_id, WeaponResolver(db, session_id))


def _run_supply(hot: InMemoryHotState, ticks: int, tick_rate_ms: int, start: int = 0) -> None:
    """跑 tick loop 真正呼叫的那個補給結算函式，共 `ticks` 個 tick 的模擬時間。

    從 `start` 起跑而不是 `start+1`：單位第一次被結算時 `tick_supply` 只把時鐘起點寫下來
    （播種端不寫 `supply_tick`），那一次不扣任何存量。
    """
    for tick in range(start, start + ticks + 1):
        _supply_tick(hot, tick, tick_rate_ms, {})


def _adjudicate(db: Session, world, hot: InMemoryHotState) -> float:  # type: ignore[no-untyped-def]
    """下一道真的 ENGAGE 令並裁決，回**目標實際掉了多少戰力**。

    每次都用全新且同種子的 RNG，於是兩次呼叫之間唯一的差別就是熱狀態。
    """
    OrderService(db, FakeGateway(visible=True)).submit(
        world.session_id,
        OrderRequest(
            unit_id=world.blue_unit_id,
            order_type=OrderType.ENGAGE,
            payload={"target_unit_id": world.red_unit_id},
        ),
        world.blue_issuer_id,
    )
    (cmd,) = _drain(db, world.session_id)
    before = float(hot.get_unit(world.red_unit_id)["strength"])
    EngagementAdjudicator(
        db,
        hot,
        DeterministicRNG(1, "adjudication"),
        lambda _cmd: _WEAPON,
        lambda _s, _t, _indirect=False: EnvSnapshot(range_m=500.0, los_clear=True),
        quantity_for=lambda _cmd: 8,  # >1 → 齊射路徑（決定性：只抽一次散布）
    ).resolve(cmd, SimTime(0, 0))
    return before - float(hot.get_unit(world.red_unit_id)["strength"])


def _drain(db: Session, session_id: str):  # type: ignore[no-untyped-def]
    import asyncio

    return asyncio.run(EngageOrderSource(db, session_id).drain())


# ---------------------------------------------------------------------------
# 中性：**這是 golden 不必重錄的唯一保證**
# ---------------------------------------------------------------------------


def test_a_scenario_that_declares_no_supply_is_bit_for_bit_untouched(
    session_factory: sessionmaker[Session],
) -> None:
    """既有想定沒有宣告補給 → 熱狀態鍵集不變 → `compute_state_hash` 不變 → golden 不必重錄。

    ⚠ 斷言的是**雜湊**而不是「沒有 supply 鍵」：golden 比的就是這個值，
    多寫任何一個鍵（哪怕是 `supply_tick: 0` 這種看起來無害的時間戳）都會讓五份 golden 全紅。
    消耗率已經不是 0 了，所以這條保證現在完全靠「有沒有宣告」撐著。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)
        assert SUPPLY_KEY not in hot.get_unit(world.blue_unit_id)
        before = compute_state_hash(hot.get_all())

        _run_supply(hot, ticks=_DAY * 5, tick_rate_ms=_MIN)  # 五個模擬日

        assert compute_state_hash(hot.get_all()) == before
        assert supply_effectiveness(hot.get_unit(world.blue_unit_id)) == 1.0


def test_refit_costs_a_legacy_session_not_even_one_db_query(
    session_factory: sessionmaker[Session],
) -> None:
    """`REPAIR_PER_DAY` 不再是 0，所以「既有局零成本」不能再靠那一行 early return。

    現在靠的是 `_is_refit_candidate`：沒有 `supply` 鍵 → 沒有 Class IX → 不是候選 →
    **`load_points` 根本不會被呼叫**。只斷言「沒有事件」殺不掉這個突變（沒有補給點時
    本來就沒有事件），所以讓 DB 一被碰就爆。
    """

    class _ExplodingDb:
        def scalars(self, *_a, **_k):  # type: ignore[no-untyped-def]
            raise AssertionError("沒有單位編制 Class IX 時不該查補給點")

    world = seed_world(session_factory)
    with session_factory() as db:
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)
        hot.update_unit(world.blue_unit_id, {"strength": 40.0})  # 有戰損，但沒有維修件

    from app.engine.refit_wiring import refit_tick

    assert (
        refit_tick(
            _ExplodingDb(), hot, world.session_id, lambda _u: None, lambda _u: "BLUE", 1, _MIN
        )
        == []
    )


# ---------------------------------------------------------------------------
# 校準錨點：Class I 的存量單位就是「補給日」
# ---------------------------------------------------------------------------


def test_a_three_day_basic_load_runs_dry_on_the_third_simulated_day(
    session_factory: sessionmaker[Session],
) -> None:
    """**錨點**：口糧/水的基本攜行量是 3 個補給日，消耗率依定義 1.0 DOS/日
    → 滿載被切斷的部隊**第 3 個模擬日**耗盡。

    這條測試釘的是「`capacity` 讀得出天數」這個語義本身：第 2 日還剩約 1 日份、
    第 3 日見底。錨點若被改動（例如把率調成 2.0），這條會紅——那正是要的。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        _declare(db, world.blue_unit_id, I=SupplyLevel(3.0, 3.0))
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)

        _run_supply(hot, ticks=_DAY * 2, tick_rate_ms=_MIN)
        assert read_levels(hot.get_unit(world.blue_unit_id))[
            SupplyClass.I
        ].on_hand == pytest.approx(1.0, abs=1e-3), "斷補兩天後該剩一天份"

        _run_supply(hot, ticks=_DAY, tick_rate_ms=_MIN, start=_DAY * 2)
        assert read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand == 0.0


def test_rations_still_run_out_when_a_tick_is_one_second(
    session_factory: sessionmaker[Session],
) -> None:
    """**1 秒/tick 的想定也要真的會餓**——官方 demo 與使用者的想定寫的都是 1000。

    熱狀態存的是四捨五入到四位小數的值，而 1 秒/tick 每 tick 只吃掉 1.2e-5 份。
    只要照樣推進 `supply_tick`，零頭每 tick 都被捨掉，口糧**永遠不會見底**
    ——而事件、畫面、單元測試全都看不出異常（V2.1 的「壓制/工事寫死 1 分鐘」同一個形狀）。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        _declare(db, world.blue_unit_id, I=SupplyLevel(3.0, 3.0))
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)

        ticks = 20_000  # 約 0.231 個模擬日
        _run_supply(hot, ticks=ticks, tick_rate_ms=_SEC)

        expected = 3.0 - ticks * _SEC / 86_400_000
        assert read_levels(hot.get_unit(world.blue_unit_id))[
            SupplyClass.I
        ].on_hand == pytest.approx(expected, abs=1e-3)


def test_fuel_and_ammo_are_deliberately_not_consumed_here() -> None:
    """III/V 留 0 是刻意的：油料走 #84、彈藥走 #44，各自有**自己的熱狀態鍵**。

    在這裡給它們非 0 值只會多出一份沒有消費端的平行帳——同一件事有兩份帳就一定會漂。
    """
    assert daily_consumption(SupplyClass.III) == 0.0
    assert daily_consumption(SupplyClass.V) == 0.0
    assert set(DAILY_CONSUMPTION) == {SupplyClass.I, SupplyClass.IX}


# ---------------------------------------------------------------------------
# SPEC 驗收條文一：斷補的部隊 3 模擬日後效能階梯下降
# ---------------------------------------------------------------------------


def test_a_cut_off_unit_actually_shoots_worse(session_factory: sessionmaker[Session]) -> None:
    """**驗收條文**：斷補的裝甲連 3 模擬日後（口糧盡）效能階梯下降。

    走完整條鏈：`TacticalUnit.attributes` → `seed_combat_state` → `_supply_tick`（tick loop
    真正呼叫的那個）→ `starved_days` → `supply_effectiveness` → `adjudicator`。
    斷言的是**觀測得到的事實**：同一發齊射，斷補後目標少掉了整整 10% 的戰力
    ——不是「modifier 等於 0.9」（那只是在複誦常數表）。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        _declare(db, world.blue_unit_id, I=SupplyLevel(3.0, 3.0))
        _arm(db, world.blue_unit_id, ammo=200)
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)
        target_before = float(hot.get_unit(world.red_unit_id)["strength"])

        fed = _adjudicate(db, world, hot)
        assert fed > 0.0, "對照組本身要真的打中，否則兩邊都是 0 也會相等"

        # 目標復原，讓兩次裁決唯一的差別是射手的補給狀態。
        hot.update_unit(world.red_unit_id, {"strength": target_before})
        # 3 日耗盡 + 再 1 日 → 斷補滿一天 → 階梯的第一階（×0.9）。
        _run_supply(hot, ticks=_DAY * 4, tick_rate_ms=_MIN)

        shooter = hot.get_unit(world.blue_unit_id)
        assert read_levels(shooter)[SupplyClass.I].on_hand == 0.0
        assert shooter[STARVED_DAYS_KEY] >= 1.0
        assert supply_effectiveness(shooter) == pytest.approx(0.9)

        starved = _adjudicate(db, world, hot)
        assert starved == pytest.approx(fed * 0.9, rel=1e-6)


def test_the_aggregate_path_feels_starvation_too(session_factory: sessionmaker[Session]) -> None:
    """`adjudicator` 有**兩個**消費點——營級以上走 Lanchester，那條路徑另外乘一次。

    漏掉它的後果不是小事：切斷一個旅的補給線會對它的戰鬥力毫無影響（這正是 #33a
    當初漏掉扣彈藥時發生的事）。
    """
    from app.models.enums import UnitLevel

    world = seed_world(session_factory)
    with session_factory() as db:
        shooter = db.get(TacticalUnit, world.blue_unit_id)
        assert shooter is not None
        shooter.unit_level = UnitLevel.BATTALION  # → should_aggregate
        db.commit()
        _declare(db, world.blue_unit_id, I=SupplyLevel(3.0, 3.0))
        _arm(db, world.blue_unit_id, ammo=200)
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)
        target_before = float(hot.get_unit(world.red_unit_id)["strength"])

        fed = _adjudicate(db, world, hot)
        assert fed > 0.0

        hot.update_unit(world.red_unit_id, {"strength": target_before})
        _run_supply(hot, ticks=_DAY * 4, tick_rate_ms=_MIN)
        starved = _adjudicate(db, world, hot)

        assert starved < fed


# ---------------------------------------------------------------------------
# SPEC 驗收條文二：打掉補給點後下游單位水位不再回升
# ---------------------------------------------------------------------------


def _add_point(db: Session, session_id: str, lat: float, lng: float, stock: dict) -> str:  # type: ignore[type-arg]
    row = MapFeature(
        session_id=session_id,
        kind=SUPPLY_POINT_KIND,
        geometry_type="POINT",
        geometry=[lng, lat],
        owner_faction="BLUE",
        influence_radius_m=0.0,
        attributes={"stock": stock},
    )
    db.add(row)
    db.commit()
    return str(row.id)


def test_destroying_the_dump_stops_the_levels_from_recovering(
    session_factory: sessionmaker[Session],
) -> None:
    """**驗收條文**：打掉補給點後下游單位水位不再回升。

    整條走生產接線：宣告在 `attributes` → `seed_combat_state` 播種 → `_supply_tick` 真的把
    存量吃到再訂購水位以下 → `_resupply_tick`（tick loop 呼叫的那一個，自己開 DB session、
    自己查 `TacticalUnit` 座標）撥交 → `destroy_at`（火力裁決在命中時呼叫的同一個函式）
    → 再跑一次 `_resupply_tick`，水位**不再回升**。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        lat, lng = float(unit.current_lat), float(unit.current_lng)
        _add_point(db, world.session_id, lat, lng, {"I": 100.0})
        _declare(db, world.blue_unit_id, I=SupplyLevel(3.0, 3.0))
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)

    # 2.5 個模擬日 → 剩 0.5 份 → 低於 30% 再訂購水位。
    _run_supply(hot, ticks=_DAY * 5 // 2, tick_rate_ms=_MIN)
    low = read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand
    assert low == pytest.approx(0.5, abs=1e-3)

    events = _resupply_tick(session_factory, hot, world.session_id, _DAY * 5 // 2)
    assert [e.event_type for e in events] == ["RESUPPLIED"]
    assert read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand == 3.0

    # 砲彈落在補給點上——`fire_wiring` 命中時呼叫的就是這個函式。
    with session_factory() as db:
        assert destroy_at(db, world.session_id, lat, lng, 500.0)
        db.commit()

    _run_supply(hot, ticks=_DAY * 5 // 2, tick_rate_ms=_MIN, start=_DAY * 5 // 2)
    drained = read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand
    assert drained == pytest.approx(0.5, abs=1e-3)

    assert _resupply_tick(session_factory, hot, world.session_id, _DAY * 5) == []
    assert read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.I].on_hand == pytest.approx(
        drained
    ), "補給線斷了，水位不該回升"


def test_a_dump_stocked_in_armoury_vocabulary_is_not_silently_empty(
    session_factory: sessionmaker[Session],
) -> None:
    """軍械庫 UI 教想定作者寫的是 FUEL/AMMO/WATER_FOOD/BATTERY，本體系講的是 Class 編號。

    對不上的鍵過去被**靜靜丟掉**：補給點寫 `{"WATER_FOOD": 100}` → 庫存空 → `usable` 是
    False → 地圖上一個補給點好端端地在那裡，卻永遠撥不出任何東西，也沒有任何訊息。
    """
    from app.engine.supply_points import load_points

    assert parse_class("WATER_FOOD") is SupplyClass.I
    assert parse_class("紅茶") is None

    world = seed_world(session_factory)
    with session_factory() as db:
        _add_point(db, world.session_id, 24.0, 121.0, {"WATER_FOOD": 60.0, "I": 40.0})
        (point,) = load_points(db, world.session_id)

    assert point.usable
    # 兩套字彙同時出現要**相加**——覆蓋的話結果會隨 dict 順序漂。
    assert point.stock[SupplyClass.I] == pytest.approx(100.0)


# ---------------------------------------------------------------------------
# 校準錨點：整補速率
# ---------------------------------------------------------------------------


def test_a_company_at_sixty_percent_is_restored_in_about_four_simulated_days(
    session_factory: sessionmaker[Session],
) -> None:
    """**錨點**：戰力 60%（損失 40 點）的部隊退到後方整補，約 4 個模擬日恢復滿編。

    走 `sim_runtime._refit_tick`（tick loop 呼叫的那一個，自己開 DB session）。
    ⚠ 也順帶釘住「第一個 tick 只計時不修」——[JCATS-A p.27]「絕非申請後直接恢復戰力」。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        unit.current_strength = 60.0
        lat, lng = float(unit.current_lat), float(unit.current_lng)
        db.commit()
        _add_point(db, world.session_id, lat, lng, {"IX": 100.0})
        _declare(db, world.blue_unit_id, IX=SupplyLevel(40.0, 40.0))
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)
        assert hot.get_unit(world.blue_unit_id)["strength"] == 60.0

    def factions(_uid: str) -> str:
        return "BLUE"  # 全場友軍 → 不會被 ENEMY_NEAR 擋下

    started = _refit_tick(session_factory, hot, world.session_id, factions, 0, _MIN, REPAIR_PER_DAY)
    assert [e.event_type for e in started] == ["REFIT_STARTED"]
    assert hot.get_unit(world.blue_unit_id)["strength"] == 60.0, "申請後不會直接恢復戰力"

    # 3 日後：60 + 30 = 90（尚未滿編）。4 日後：夾在 100。
    _refit_tick(session_factory, hot, world.session_id, factions, _DAY * 3, _MIN, REPAIR_PER_DAY)
    assert hot.get_unit(world.blue_unit_id)["strength"] == pytest.approx(90.0, abs=1e-2)

    _refit_tick(session_factory, hot, world.session_id, factions, _DAY * 4, _MIN, REPAIR_PER_DAY)
    assert hot.get_unit(world.blue_unit_id)["strength"] == 100.0
    # 料件是硬上限，而且真的被扣了：40 點 × 0.5 ＝ 20。
    parts = read_levels(hot.get_unit(world.blue_unit_id))[SupplyClass.IX]
    assert parts.on_hand == pytest.approx(40.0 - 40.0 * PARTS_PER_POINT, abs=1e-2)


def test_repair_still_progresses_when_a_tick_is_one_second(
    session_factory: sessionmaker[Session],
) -> None:
    """1 秒/tick 每 tick 只修 1.2e-4 點——比熱狀態的三位小數解析度還小。

    照樣推進 `refit_tick` 的時間戳就等於每 tick 把零頭捨掉，戰力**永遠停在原地**，
    而 `REFIT_PROGRESS` 事件與畫面都看不出異常。不推進，經過時間就會累積到修得出
    可觀測的一點為止。
    """
    world = seed_world(session_factory)
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.blue_unit_id)
        assert unit is not None
        unit.current_strength = 60.0
        lat, lng = float(unit.current_lat), float(unit.current_lng)
        db.commit()
        _add_point(db, world.session_id, lat, lng, {"IX": 100.0})
        _declare(db, world.blue_unit_id, IX=SupplyLevel(40.0, 40.0))
        hot = InMemoryHotState()
        _seed(db, world.session_id, hot)

    def factions(_uid: str) -> str:
        return "BLUE"

    for tick in range(0, 2001):
        _refit_tick(session_factory, hot, world.session_id, factions, tick, _SEC, REPAIR_PER_DAY)

    state = hot.get_unit(world.blue_unit_id)
    # 2000 秒 ≈ 0.02315 日 × 10 點/日 ≈ 0.2315 點。
    assert state["strength"] > 60.0
    assert state["strength"] == pytest.approx(60.0 + 2000 * _SEC / 86_400_000 * 10.0, abs=5e-3)
    assert state[REFIT_TICK_KEY] is not None
