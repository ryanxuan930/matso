"""MSEL 執行器與擴充的條件 DSL（WP-B2）。

**動手前 MSEL 是死碼**：`MselEngine.check()` 只回 LedgerEvent，而活執行期傳的是
`NoOpTriggerChecker`——想定裡寫的 MSEL 條目從來沒有跑過。
"""

from __future__ import annotations

import pytest

from app.scenario.msel_runtime import MselMemory, MselRuntime, evaluate_msel
from app.scenario.triggers import (
    MselEntry,
    TriggerContext,
    TriggerError,
    evaluate_condition,
    validate_condition,
)
from app.state.ledger import LedgerEvent


def _ctx(tick: int = 0, **kw: object) -> TriggerContext:
    return TriggerContext(tick=tick, **kw)  # type: ignore[arg-type]


def _entry(eid: str, trigger: dict, once: bool = True) -> MselEntry:  # type: ignore[type-arg]
    return MselEntry(id=eid, trigger=trigger, inject={"event_type": "INJECT"}, once=once)


# ---- 新條件型別 ----


def test_not_inverts() -> None:
    cond = {"type": "not", "of": {"type": "time", "at_tick": 10}}
    assert evaluate_condition(cond, _ctx(tick=5)) is True
    assert evaluate_condition(cond, _ctx(tick=10)) is False


def test_unit_in_polygon() -> None:
    """真多邊形——bbox 表達不了「河岸以北」這種形狀。"""
    square = [[121.0, 24.0], [121.1, 24.0], [121.1, 24.1], [121.0, 24.1]]
    cond = {"type": "unit_in_polygon", "faction": "RED", "polygon": square}
    assert evaluate_condition(cond, _ctx(unit_positions=[("RED", 24.05, 121.05)])) is True
    assert evaluate_condition(cond, _ctx(unit_positions=[("RED", 24.05, 121.5)])) is False
    # 陣營要對——別人的單位站進去不算。
    assert evaluate_condition(cond, _ctx(unit_positions=[("BLUE", 24.05, 121.05)])) is False


def test_contact_established() -> None:
    cond = {"type": "contact_established", "faction": "BLUE", "of": "RED"}
    assert evaluate_condition(cond, _ctx(contacts=frozenset({("BLUE", "RED")}))) is True
    # **方向有意義**：紅軍看到藍軍，不代表藍軍看到紅軍。
    assert evaluate_condition(cond, _ctx(contacts=frozenset({("RED", "BLUE")}))) is False


def test_manual_never_fires_on_its_own() -> None:
    """`manual` 是白軍的動態取捨機制——時間到就自動發的話，那就不是取捨了。"""
    cond = {"type": "manual"}
    assert evaluate_condition(cond, _ctx(tick=99999, entry_id="e1")) is False
    assert evaluate_condition(cond, _ctx(entry_id="e1", manual_fired=frozenset({"e1"}))) is True


def test_after_ticks_of() -> None:
    cond = {"type": "after_ticks_of", "event": "e1", "ticks": 20}
    assert evaluate_condition(cond, _ctx(tick=50)) is False  # e1 還沒觸發
    assert evaluate_condition(cond, _ctx(tick=50, fired_at={"e1": 40})) is False
    assert evaluate_condition(cond, _ctx(tick=60, fired_at={"e1": 40})) is True


# ---- 載入時驗證 ----


def test_unknown_type_is_caught_at_load_time() -> None:
    """想定資產的錯誤要在**載入時**指出精確路徑，不是跑到一半才靜默失效。"""
    with pytest.raises(TriggerError, match="未知的 condition type"):
        validate_condition({"type": "telepathy"}, "msel[0].trigger")


def test_not_requires_a_single_condition_not_a_list() -> None:
    """`not`/`held_for` 的 `of` 是單一條件，`all`/`any` 才是陣列——搞混要在載入時說。"""
    with pytest.raises(TriggerError):
        validate_condition({"type": "not", "of": [{"type": "manual"}]}, "x")


def test_polygon_needs_three_vertices() -> None:
    with pytest.raises(TriggerError, match="至少要三個頂點"):
        validate_condition(
            {"type": "unit_in_polygon", "faction": "RED", "polygon": [[1, 2], [3, 4]]}, "x"
        )


def test_nested_validation_reports_the_path() -> None:
    with pytest.raises(TriggerError, match=r"x\.all\.of\[1\]"):
        validate_condition(
            {"type": "all", "of": [{"type": "manual"}, {"type": "nope"}]},
            "x",
        )


# ---- held_for 的連續計時 ----


def test_held_for_needs_continuous_satisfaction() -> None:
    """「成立→中斷→再成立」要**重新計時**——那才是「持續 N tick」，不是「累計成立 N 次」。"""
    entry = _entry(
        "e1",
        {
            "type": "held_for",
            "ticks": 3,
            "of": {"type": "strength_below", "faction": "RED", "value": 50},
        },
    )
    mem = MselMemory()
    weak = {"RED": 20.0}
    strong = {"RED": 90.0}

    assert evaluate_msel([entry], _ctx(0, faction_strength=weak), mem) == []
    assert evaluate_msel([entry], _ctx(1, faction_strength=weak), mem) == []
    # 中斷——計時歸零
    assert evaluate_msel([entry], _ctx(2, faction_strength=strong), mem) == []
    assert evaluate_msel([entry], _ctx(3, faction_strength=weak), mem) == []
    assert evaluate_msel([entry], _ctx(5, faction_strength=weak), mem) == []
    out = evaluate_msel([entry], _ctx(6, faction_strength=weak), mem)
    assert [d.entry_id for d in out] == ["e1"]


# ---- 引擎行為 ----


def test_once_entries_fire_exactly_once() -> None:
    entry = _entry("e1", {"type": "time", "at_tick": 5})
    mem = MselMemory()
    assert len(evaluate_msel([entry], _ctx(5), mem)) == 1
    assert evaluate_msel([entry], _ctx(6), mem) == []


def test_memory_survives_a_round_trip() -> None:
    """**記憶必須進 checkpoint**——`MselEngine._fired` 是個純記憶體的 set，
    每次 runner 重啟就把所有 once 條目重新武裝。那是已知缺陷，這裡不重蹈。"""
    mem = MselMemory()
    evaluate_msel([_entry("e1", {"type": "time", "at_tick": 0})], _ctx(0), mem)
    restored = MselMemory.from_dict(mem.to_dict())
    assert restored.fired_at == {"e1": 0}
    assert evaluate_msel([_entry("e1", {"type": "time", "at_tick": 0})], _ctx(9), restored) == []


def test_evaluation_order_is_deterministic() -> None:
    entries = [_entry(f"e{i}", {"type": "time", "at_tick": 0}) for i in (3, 1, 2)]
    out = evaluate_msel(entries, _ctx(0), MselMemory())
    assert [d.entry_id for d in out] == ["e1", "e2", "e3"]


def test_a_failing_inject_does_not_crash_the_tick() -> None:
    """**kernel 的 trigger 槽沒有任何防護**——一個例外會讓 runner 崩潰後每 3 秒被重建。

    一則注入壞掉要落一筆 `MSEL_INJECT_FAILED`，不是往上拋。
    """

    def boom(entry_id: str, inject: dict, tick: int) -> list[LedgerEvent]:  # type: ignore[type-arg]
        raise RuntimeError("想定資料有問題")

    rt = MselRuntime([_entry("e1", {"type": "time", "at_tick": 0})], lambda t: _ctx(t), boom)
    events = rt.check(type("T", (), {"tick": 0})())
    assert [e.event_type for e in events] == ["INJECT", "MSEL_INJECT_FAILED"]


def test_no_msel_entries_means_no_work() -> None:
    """沒有 MSEL 的局完全不動作——既有局零行為變更。"""
    rt = MselRuntime([], lambda t: _ctx(t))
    assert rt.check(type("T", (), {"tick": 5})()) == []


def test_white_cell_can_fire_and_skip() -> None:
    rt = MselRuntime(
        [_entry("m1", {"type": "manual"}), _entry("m2", {"type": "manual"})],
        lambda t: _ctx(t),
    )
    now = type("T", (), {"tick": 3})()
    assert rt.check(now) == []  # manual 不會自己成立
    assert rt.pending() == ["m1", "m2"]

    rt.skip("m2")  # 白軍決定不發這個狀況
    rt.fire_manually("m1")
    events = rt.check(now)
    assert [e.ai_decision["msel_id"] for e in events] == ["m1"]
    assert rt.pending() == []  # m1 已觸發、m2 已跳過


def test_skipped_entries_are_remembered_not_deleted() -> None:
    """AAR 要看得出「原定 vs 實際」——跳過是紀錄，不是把條目抹掉。"""
    rt = MselRuntime([_entry("m1", {"type": "manual"})], lambda t: _ctx(t))
    rt.skip("m1")
    assert "m1" in rt.memory.to_dict()["skipped"]


# ---- 注入的套用（把「觸發了」變成「世界真的改變了」）----


def test_modify_unit_writes_both_hot_state_and_db(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**一定要雙寫**——只寫熱狀態的話，runner 一重啟 `seed_combat_state`
    就用 DB 的舊值蓋回去（BL-4 那個回滾 bug 的同一個坑）。"""
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit
    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    hot = InMemoryHotState()
    hot.put_unit(world.red_unit_id, {"lat": 23.76, "lng": 121.26, "strength": 100.0})
    applier = make_applier(world.session_id, session_factory, hot)

    events = applier(
        "m1",
        {"action": "MODIFY_UNIT", "unit_id": world.red_unit_id, "strength": 30.0},
        50,
    )
    assert [e.event_type for e in events] == ["MSEL_UNIT_MODIFIED"]
    assert (hot.get_unit(world.red_unit_id) or {})["strength"] == 30.0
    with session_factory() as db:
        unit = db.get(TacticalUnit, world.red_unit_id)
        assert unit is not None and unit.current_strength == 30.0


def test_message_injection_lands_in_the_inbox(session_factory) -> None:  # type: ignore[no-untyped-def]
    """白軍誘導迴圈的「狀況發佈」——一則狀況要真的進到某陣營的信文匣。"""
    from _order_fakes import seed_world

    from app.models.tables import Message
    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    applier = make_applier(world.session_id, session_factory, InMemoryHotState())
    events = applier(
        "m2",
        {"action": "MESSAGE", "faction": "BLUE", "to_seat": "COMMANDER", "body": "敵砲擊北岸"},
        60,
    )
    assert events[0].ai_decision["observer_faction"] == "BLUE"  # 受眾：只有收信陣營
    with session_factory() as db:
        msgs = list(db.query(Message).filter(Message.session_id == world.session_id).all())
    assert any(m.body == "敵砲擊北岸" and m.to_faction == "BLUE" for m in msgs)


def test_unsupported_action_says_so_instead_of_pretending(session_factory) -> None:  # type: ignore[no-untyped-def]
    """`WEATHER_OVERRIDE` 還沒接（屬 WP-C4）。**靜靜什麼都不做會讓想定作者以為天氣改了。**"""
    from _order_fakes import seed_world

    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    applier = make_applier(world.session_id, session_factory, InMemoryHotState())
    events = applier("m3", {"action": "WEATHER_OVERRIDE", "preset": "STORM"}, 70)
    assert [e.event_type for e in events] == ["MSEL_INJECT_UNSUPPORTED"]


def test_a_plain_inject_needs_no_action(session_factory) -> None:  # type: ignore[no-untyped-def]
    """沒有 action ＝ 純事件注入，那是 MSEL 最原始的用法（只落一筆帳給人看）。"""
    from _order_fakes import seed_world

    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    applier = make_applier(world.session_id, session_factory, InMemoryHotState())
    assert applier("m4", {"event_type": "SITREP"}, 80) == []


def test_pause_injection_pulls_the_flag(session_factory) -> None:  # type: ignore[no-untyped-def]
    """PAUSE 與白軍控制台共用同一個暫停旗標——兩套暫停就是兩套會打架的狀態。"""
    from _order_fakes import seed_world

    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    pulled: list[bool] = []
    applier = make_applier(
        world.session_id, session_factory, InMemoryHotState(), pause=lambda: pulled.append(True)
    )
    events = applier("m5", {"action": "PAUSE", "reason": "講評"}, 90)
    assert pulled == [True]
    assert [e.event_type for e in events] == ["MSEL_PAUSE"]


# ---- SPAWN_UNITS（B2b）----


def test_spawned_unit_ids_are_deterministic() -> None:
    """**禁 uuid4()**：重播必須生出同一批 id，否則之後所有指涉那些單位的事件都對不上
    ——重播裡「增援 3 號被擊毀」會指向一個不存在的單位。"""
    from app.scenario.msel_actions import spawn_unit_id

    assert spawn_unit_id("reinforce-d2", 0) == spawn_unit_id("reinforce-d2", 0)
    assert spawn_unit_id("reinforce-d2", 0) != spawn_unit_id("reinforce-d2", 1)
    assert spawn_unit_id("other", 0) != spawn_unit_id("reinforce-d2", 0)


def test_spawn_units_creates_units_in_db_and_hot_state(session_factory) -> None:  # type: ignore[no-untyped-def]
    """生成的單位**要播進熱狀態**——地圖、裁決、MSEL 脈絡讀的都是熱狀態，
    只寫 DB 的話那支部隊在模擬裡等於不存在。"""
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit
    from app.scenario.msel_actions import make_applier, spawn_unit_id
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    hot = InMemoryHotState()
    applier = make_applier(world.session_id, session_factory, hot)
    events = applier(
        "reinforce",
        {
            "action": "SPAWN_UNITS",
            "faction": "RED",
            "units": [
                {"designation": "R-REINF-1", "lat": 23.9, "lng": 121.4, "strength": 120.0},
                {"designation": "R-REINF-2", "lat": 23.91, "lng": 121.41},
            ],
        },
        200,
    )
    assert [e.event_type for e in events] == ["MSEL_UNITS_SPAWNED"]
    uid0 = spawn_unit_id("reinforce", 0)
    with session_factory() as db:
        unit = db.get(TacticalUnit, uid0)
        assert unit is not None and unit.faction == "RED" and unit.designation == "R-REINF-1"
    assert (hot.get_unit(uid0) or {})["strength"] == 120.0


def test_spawning_twice_does_not_duplicate(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**冪等**：白軍重複扣板機、或重啟後記憶沒還原，都不該生出兩批增援。"""
    from _order_fakes import seed_world

    from app.models.tables import TacticalUnit
    from app.scenario.msel_actions import make_applier
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    applier = make_applier(world.session_id, session_factory, InMemoryHotState())
    inject = {
        "action": "SPAWN_UNITS",
        "faction": "RED",
        "units": [{"designation": "R-DUP", "lat": 23.9, "lng": 121.4}],
    }
    applier("dup", inject, 10)
    assert applier("dup", inject, 20) == []  # 第二次什麼都不生
    with session_factory() as db:
        n = db.query(TacticalUnit).filter(TacticalUnit.designation == "R-DUP").count()
    assert n == 1


def test_a_lazily_spawned_unit_can_find_its_weapons(session_factory) -> None:  # type: ignore[no-untyped-def]
    """**SPEC 明列的陷阱**：resolver 的快取是 runner 啟動當下的世界。

    沒有惰性補查的話，MSEL 生出來的增援會出現在地圖上、下得了令、
    卻一發都打不出去——而且沒有任何錯誤訊息。
    """
    from _order_fakes import seed_world

    from app.engine.engage_wiring import WeaponResolver
    from app.models.tables import EquipmentTemplate
    from app.scenario.msel_actions import make_applier, spawn_unit_id
    from app.state.hot_state import InMemoryHotState

    world = seed_world(session_factory)
    with session_factory() as db:
        db.add(
            EquipmentTemplate(
                name="REINF_RIFLE",
                category="KINETIC",
                base_stats={
                    "max_range_m": 600,
                    "ph_by_range_band": [[600, 0.3]],
                    "damage_by_armor_class": {"INFANTRY": 30},
                    "ammo_types": ["A"],
                },
            )
        )
        db.commit()
        # resolver 在增援出現**之前**建好——正是活執行期的狀況。
        resolver = WeaponResolver(db, world.session_id)
    resolver.enable_lazy_lookup(session_factory)

    applier = make_applier(world.session_id, session_factory, InMemoryHotState())
    applier(
        "late",
        {
            "action": "SPAWN_UNITS",
            "faction": "RED",
            "units": [
                {
                    "designation": "R-LATE",
                    "lat": 23.9,
                    "lng": 121.4,
                    "equipment": [{"template": "REINF_RIFLE", "quantity": 1, "ammo": 100}],
                }
            ],
        },
        300,
    )
    weapons = resolver.weapons_for(spawn_unit_id("late", 0))
    assert [w.template_name for w in weapons] == ["REINF_RIFLE"]
