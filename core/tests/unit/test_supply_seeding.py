"""補給播種鏈（WP-C7）：想定 → LoadedScenario → TacticalUnit.attributes → 熱狀態。

**這批測試一律走生產接線**：真的想定檔 → `create_session_from_scenario` → `seed_combat_state`。
沒有任何一條 `hot.put_unit(...)` 自己餵資料——C7.1/C7.2/C7.3 三張卡的測試全綠卻整條鏈
一次都執行不到，正是因為那些測試自己把 `supply` 塞進熱狀態，繞過了真正缺的那一層。

保護的三件事：
1. **宣告的要到得了熱狀態**（否則整套後勤是裝飾）。
2. **沒宣告的一個鍵都不能多**（熱狀態鍵集一變，`compute_state_hash` 就變，五份 golden 全要重錄）。
3. **同一份宣告播兩次要位元相同**（類別順序若隨 dict 順序漂，同一個世界會算出兩種雜湊）。
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select

from app.adjudication.supply import REORDER_LEVEL, SupplyClass
from app.engine.engage_wiring import WeaponResolver, seed_combat_state
from app.engine.supply_points import DRAW_RADIUS_M, load_points, nearest_usable
from app.engine.supply_wiring import SUPPLY_KEY, SUPPLY_TICK_KEY, tick_supply
from app.models import TacticalUnit
from app.scenario import create_session_from_scenario, load_scenario_package
from app.state.hot_state import InMemoryHotState

_EXAMPLES = Path(__file__).resolve().parents[3] / "scenarios" / "examples"
_ARMOR = _EXAMPLES / "armor-breakthrough"
# 不宣告後勤的官方想定——中性對照組（golden 保護的那條路徑）。
_NEUTRAL = _EXAMPLES / "battalion-defense"

_TICK_MS = 60_000  # 本想定 tick_rate_ms＝1 分鐘
_DAY_TICKS = 1440


def _open(package: Path, factory: Any) -> tuple[str, InMemoryHotState, Any]:
    """真的載入一份想定 → 開局 → 播戰鬥狀態。回 (session_id, 熱狀態, db)。"""
    loaded = load_scenario_package(package)
    db = factory()
    session_id = create_session_from_scenario(db, loaded, master_seed=1)
    hot = InMemoryHotState()
    seed_combat_state(db, hot, session_id, WeaponResolver(db, session_id))
    return session_id, hot, db


def _state_of(db: Any, hot: InMemoryHotState, session_id: str, designation: str) -> dict[str, Any]:
    unit = db.scalar(
        select(TacticalUnit).where(
            TacticalUnit.session_id == session_id, TacticalUnit.designation == designation
        )
    )
    assert unit is not None, f"想定裡沒有 {designation}——測試自己壞了，先修它"
    return dict(hot.get_unit(unit.id) or {})


# ── 1. 宣告要到得了熱狀態 ────────────────────────────────────────────────


def test_a_declared_unit_carries_its_supply_all_the_way_into_hot_state(session_factory) -> None:  # type: ignore[no-untyped-def]
    """播種鏈端到端：想定檔的數字要**逐位**出現在熱狀態，中間四段都不准掉。

    `[存量, 容量]` 的形狀就是 `supply_wiring.read_levels` 讀的形狀——裁決層讀的也是它。
    """
    sid, hot, db = _open(_ARMOR, session_factory)

    # 滿載出發（想定省略 on_hand ＝ 等於 capacity）。
    assert _state_of(db, hot, sid, "B-3-A")[SUPPLY_KEY] == {"I": [3.0, 3.0], "IX": [20.0, 20.0]}
    # 明確宣告的短缺要照樣帶過來，不能被「滿載」的預設蓋掉。
    assert _state_of(db, hot, sid, "B-1-C")[SUPPLY_KEY] == {"I": [1.2, 3.0], "IX": [8.0, 20.0]}
    # 排級的量與連級不同——播的是各單位自己的宣告，不是一份共用預設。
    assert _state_of(db, hot, sid, "R-1-1")[SUPPLY_KEY] == {"I": [2.0, 2.0], "IX": [6.0, 6.0]}


def test_the_declaration_lands_on_the_unit_row_not_only_in_hot_state(session_factory) -> None:  # type: ignore[no-untyped-def]
    """熱狀態是**會被清掉**的（Redis 掛了、換機器、重開局）。宣告必須存在 DB 的單位列上，
    播種才有東西可讀——只寫熱狀態的話，重啟一次全軍的補給編制就人間蒸發。"""
    sid, _hot, db = _open(_ARMOR, session_factory)
    unit = db.scalar(
        select(TacticalUnit).where(
            TacticalUnit.session_id == sid, TacticalUnit.designation == "B-3-A"
        )
    )
    assert unit.attributes[SUPPLY_KEY] == {"I": [3.0, 3.0], "IX": [20.0, 20.0]}


def test_the_seeded_unit_really_starts_eating(session_factory) -> None:  # type: ignore[no-untyped-def]
    """播完之後，**真正的消費端**要能把它結算掉——否則播了也只是一份資料。

    這條走的是生產流程：播種 → 第一個 tick（`tick_supply` 自己落下結算起點）→
    一個模擬日之後再結算 → 存量真的少了。中間**沒有任何一步是測試自己餵的**。
    ⚠ 若哪天 `tick_supply` 又退回「缺 `supply_tick` 就回 None」，本條會紅——
    那正是整條鏈死掉而三張卡的單元測試照樣全綠的那個形狀。
    """
    sid, hot, db = _open(_ARMOR, session_factory)
    unit = db.scalar(
        select(TacticalUnit).where(
            TacticalUnit.session_id == sid, TacticalUnit.designation == "B-3-A"
        )
    )
    before = hot.get_unit(unit.id)[SUPPLY_KEY]["I"][0]
    assert SUPPLY_TICK_KEY not in hot.get_unit(unit.id)  # 播種端不碰結算起點

    for tick in (1, 1 + _DAY_TICKS):
        patch = tick_supply(hot, unit.id, tick, _TICK_MS)
        assert patch is not None, f"tick {tick}：播完的單位結算回 None ＝ 這條鏈沒有起點"
        hot.update_unit(unit.id, patch)

    # 消耗率的錨點是「1 存量單位 ＝ 1 個補給日」（見 `adjudication/supply.DAILY_CONSUMPTION`），
    # 故一個模擬日之後 Class I 恰好少 1——**斷補三日耗盡**那條驗收條文就是這樣成立的。
    assert hot.get_unit(unit.id)[SUPPLY_KEY]["I"][0] == pytest.approx(before - 1.0)


def test_supply_points_land_as_features_the_engine_can_find(session_factory) -> None:  # type: ignore[no-untyped-def]
    """想定的補給點要變成 `load_points()` 找得到的東西，且**座標不能顛倒**。

    `[lng, lat]` 寫反了不會報錯——`nearest_usable` 只會算出幾千公里的距離，於是每個單位
    都補不到貨，而畫面上什麼徵兆都沒有。故斷言每個點都落在想定的 bbox 內。
    """
    sid, _hot, db = _open(_ARMOR, session_factory)
    points = load_points(db, sid)
    assert {p.faction for p in points} == {"BLUE", "RED"}
    assert len(points) == 3

    min_lng, min_lat, max_lng, max_lat = load_scenario_package(_ARMOR).bbox
    for p in points:
        assert min_lat <= p.lat <= max_lat, f"{p.feature_id} 的緯度不在 bbox 內——座標顛倒了"
        assert min_lng <= p.lng <= max_lng
        assert p.usable  # 有貨、沒被摧毀
    assert {c for p in points for c in p.stock} == {SupplyClass.I, SupplyClass.IX}


def test_the_declared_units_are_actually_within_reach_of_their_own_points(session_factory) -> None:  # type: ignore[no-untyped-def]
    """想定內容的驗收：宣告了補給、卻沒有一個點在撥交半徑內的話，那份宣告是裝飾。

    藍軍裝甲營靠**前進補給站**（旅補給點在 4 km 外，對它等於不存在）——這正是
    「打掉前進補給站，逆襲部隊就斷補」這條戰法在本想定成立的前提。
    """
    sid, _hot, db = _open(_ARMOR, session_factory)
    points = load_points(db, sid)
    by_designation = {
        u.designation: u
        for u in db.scalars(select(TacticalUnit).where(TacticalUnit.session_id == sid))
    }
    for designation, faction in (("B-3-A", "BLUE"), ("B-3-B", "BLUE"), ("R-1-A", "RED")):
        unit = by_designation[designation]
        assert nearest_usable(points, faction, unit.current_lat, unit.current_lng) is not None, (
            f"{designation} 宣告了補給，但 {DRAW_RADIUS_M / 1000} km 內沒有己方補給點"
        )
    # 前緣的反裝甲連**刻意**在半徑外（後勤官要處理的問題），不是忘了放點。
    b1c = by_designation["B-1-C"]
    assert nearest_usable(points, "BLUE", b1c.current_lat, b1c.current_lng) is None


def test_no_declared_unit_starts_below_its_reorder_level(session_factory) -> None:  # type: ignore[no-untyped-def]
    """想定內容的驗收：開局就低於再訂購水位的單位會在第一個 tick 申請補給，而拉不到時
    執行期是**安靜地什麼都不做**（沒有事件）。官方想定不該示範一個看起來像壞掉的狀態。"""
    _sid, hot, _db = _open(_ARMOR, session_factory)
    for state in hot.get_all().values():
        for supply_class, (on_hand, capacity) in (state.get(SUPPLY_KEY) or {}).items():
            assert on_hand / capacity >= REORDER_LEVEL, f"{supply_class} 開局即低於再訂購水位"


# ── 2. 沒宣告的一個鍵都不能多（golden 保護） ──────────────────────────────


def test_a_unit_without_a_declaration_gets_no_supply_keys(session_factory) -> None:  # type: ignore[no-untyped-def]
    """同一份想定裡的中性對照：沒宣告的單位**兩個鍵都不能出現**。

    給每個單位一個預設水位是最容易寫、也最糟的做法：熱狀態鍵集一變，
    `compute_state_hash` 跟著變，五份 golden 全部要重錄。
    """
    sid, hot, db = _open(_ARMOR, session_factory)
    state = _state_of(db, hot, sid, "B-1-A")  # 機步連，未宣告
    assert SUPPLY_KEY not in state
    assert SUPPLY_TICK_KEY not in state
    assert state["strength"] > 0  # 其餘鍵照播——沒宣告補給不影響任何別的東西


def test_a_scenario_that_declares_nothing_leaves_every_unit_untouched(session_factory) -> None:  # type: ignore[no-untyped-def]
    """既有想定（三張都沒有 supply 宣告）跑完播種後，**全場沒有任何補給鍵**。

    這條就是 golden 不必重錄的那個保證，打在接線層而不是純函數層
    ——WP-C3 的 `mounted` 就是在接線這一層栽的（缺鍵被 `bool()` 收成 False）。
    """
    _sid, hot, _db = _open(_NEUTRAL, session_factory)
    assert hot.get_all(), "對照組想定一個單位都沒播進來——測試自己壞了"
    for unit_id, state in hot.get_all().items():
        assert SUPPLY_KEY not in state, f"{unit_id} 憑空多了補給鍵"
        assert SUPPLY_TICK_KEY not in state


def test_a_running_unit_is_not_reset_to_its_declaration_on_restart(session_factory) -> None:  # type: ignore[no-untyped-def]
    """執行期重啟：熱狀態已有扣過的水位 → 播種不得把它重置回想定初值
    （與 health/ammo 同紀律；否則每次 runner 重啟全軍就自動補滿）。"""
    loaded = load_scenario_package(_ARMOR)
    db = session_factory()
    sid = create_session_from_scenario(db, loaded, master_seed=1)
    unit = db.scalar(
        select(TacticalUnit).where(
            TacticalUnit.session_id == sid, TacticalUnit.designation == "B-3-A"
        )
    )
    hot = InMemoryHotState()
    hot.put_unit(unit.id, {SUPPLY_KEY: {"I": [0.5, 3.0]}, SUPPLY_TICK_KEY: 900})
    seed_combat_state(db, hot, sid, WeaponResolver(db, sid))
    state = hot.get_unit(unit.id)
    assert state[SUPPLY_KEY] == {"I": [0.5, 3.0]}  # 保留執行期已扣量
    assert state[SUPPLY_TICK_KEY] == 900  # 結算起點也不能被推回 0


# ── 3. 播兩次要位元相同 ──────────────────────────────────────────────────


def test_seeding_the_same_declaration_twice_is_byte_identical(session_factory) -> None:  # type: ignore[no-untyped-def]
    """同一份宣告播兩次，熱狀態片段位元相同——熱狀態會進 `compute_state_hash`。"""
    first_sid, first_hot, first_db = _open(_ARMOR, session_factory)
    second_sid, second_hot, second_db = _open(_ARMOR, session_factory)
    for designation in ("B-3-A", "B-1-C", "R-1-1"):
        a = _state_of(first_db, first_hot, first_sid, designation)[SUPPLY_KEY]
        b = _state_of(second_db, second_hot, second_sid, designation)[SUPPLY_KEY]
        assert a == b
        assert list(a) == list(b)  # 連鍵序都要一樣（dict 相等不看順序，雜湊看）


def test_class_order_does_not_follow_the_order_they_were_declared_in(session_factory) -> None:  # type: ignore[no-untyped-def]
    """類別順序必須由 `write_levels` 決定，**不可以隨宣告的書寫順序漂**。

    同一個世界算出兩種雜湊會讓決定性重播對不起來，而症狀是「重播結果偶爾不一致」
    ——那是最難查的一種。
    """
    loaded = load_scenario_package(_ARMOR)
    target = next(u for u in loaded.units if u.designation == "B-3-A")
    # 把宣告順序倒過來（模擬 YAML 先寫 IX 再寫 I），其餘完全相同。
    flipped = dataclasses.replace(target, supply=tuple(reversed(target.supply)))
    loaded.units = [flipped if u is target else u for u in loaded.units]

    db = session_factory()
    sid = create_session_from_scenario(db, loaded, master_seed=1)
    hot = InMemoryHotState()
    seed_combat_state(db, hot, sid, WeaponResolver(db, sid))
    assert list(_state_of(db, hot, sid, "B-3-A")[SUPPLY_KEY]) == ["I", "IX"]


def test_seeding_sorts_even_when_the_attributes_arrive_unsorted(session_factory) -> None:  # type: ignore[no-untyped-def]
    """播種端**自己也要排序**，不能靠「載入器已經排好了」。

    `attributes` 不是只有載入器寫得到：`PATCH /sessions/{id}/units/{uid}`（白軍的 ORBAT 編輯）
    是 `{**舊的, **送來的}` 整包合併，送什麼順序就存什麼順序。上一條測試走的是載入器那一段，
    抓不到這裡——**突變測試就是這樣抓到的**（把播種端改成自己組 dict，上一條照樣全綠）。
    """
    sid, _hot, db = _open(_ARMOR, session_factory)
    unit = db.scalar(
        select(TacticalUnit).where(
            TacticalUnit.session_id == sid, TacticalUnit.designation == "B-3-A"
        )
    )
    unit.attributes = {SUPPLY_KEY: {"IX": [20.0, 20.0], "I": [3.0, 3.0]}}  # 反序寫入
    db.commit()

    hot = InMemoryHotState()  # 全新熱狀態＝重開局／Redis 清空後的重新播種
    seed_combat_state(db, hot, sid, WeaponResolver(db, sid))
    assert list(hot.get_unit(unit.id)[SUPPLY_KEY]) == ["I", "IX"]


# ── 4. 宣告本身的守門（沉默失效） ────────────────────────────────────────


def _bundle(scenario: dict[str, Any], units: list[dict[str, Any]]) -> dict[str, Any]:
    return {"scenario": scenario, "orbat": {"BLUE": {"faction": "BLUE", "units": units}}}


def _minimal_scenario() -> dict[str, Any]:
    return {
        "name": "t",
        "version": "1",
        "bbox": [120.0, 23.0, 121.0, 24.0],
        "mode": "REALTIME",
        "tick_rate_ms": 60000,
        "factions": [{"id": "BLUE"}],
        "victory_conditions": [{"faction": "BLUE", "condition": {"type": "time", "at_tick": 1}}],
    }


def test_on_hand_above_capacity_is_rejected_with_an_exact_path() -> None:
    """容量是編制上限，撥交一律夾在它以下——超出的部分會在第一次補給後靜靜消失。"""
    from app.scenario.loader import ScenarioError, load_scenario_bundle

    bundle = _bundle(
        _minimal_scenario(),
        [
            {
                "designation": "B1",
                "unit_level": "COMPANY",
                "supply": {"I": {"capacity": 3, "on_hand": 5}},
            }
        ],
    )
    with pytest.raises(ScenarioError, match=r"units\[0\]\.supply\.I"):
        load_scenario_bundle(bundle)


def test_an_unowned_supply_class_is_rejected_by_the_contract() -> None:
    """III（油料）與 V（彈藥）**不收**：它們已有各自的模型，在這裡宣告只會多出一份
    沒有任何消費端的平行帳——車照樣依 #84 的油耗跑，而想定作者以為自己調了油料。"""
    from app.scenario.loader import ScenarioError, load_scenario_bundle

    bundle = _bundle(
        _minimal_scenario(),
        [{"designation": "B1", "unit_level": "COMPANY", "supply": {"III": {"capacity": 3}}}],
    )
    with pytest.raises(ScenarioError, match="supply"):
        load_scenario_bundle(bundle)


def test_a_supply_point_with_an_unknown_faction_is_rejected() -> None:
    """`nearest_usable` 只找同陣營的點——打錯字的陣營＝沒有任何單位補得到，而且不會報錯。"""
    from app.scenario.loader import ScenarioError, load_scenario_bundle

    scenario = _minimal_scenario()
    scenario["supply_points"] = [
        {"name": "x", "faction": "BLEU", "lat": 23.5, "lng": 120.5, "stock": {"I": 10}}
    ]
    with pytest.raises(ScenarioError, match="未宣告的陣營"):
        load_scenario_bundle(_bundle(scenario, [{"designation": "B1", "unit_level": "COMPANY"}]))


def test_a_supply_point_with_nothing_in_it_is_rejected() -> None:
    """空的補給點恆為不可用，而且**補不回來**——撥交是「拉」不是「推」。"""
    from app.scenario.loader import ScenarioError, load_scenario_bundle

    scenario = _minimal_scenario()
    scenario["supply_points"] = [
        {"name": "空點", "faction": "BLUE", "lat": 23.5, "lng": 120.5, "stock": {"I": 0, "IX": 0}}
    ]
    with pytest.raises(ScenarioError, match="永遠不可用"):
        load_scenario_bundle(_bundle(scenario, [{"designation": "B1", "unit_level": "COMPANY"}]))
