"""Checkpoint 序列化 / hash / recover / rollback 單元測試（SQLite in-memory，不需 compose）。

session_factory 由 core/tests/conftest.py 提供；no-op Kernel 由 build_noop_kernel 提供。
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

import app.state.checkpoint as checkpoint_mod
from app.engine.kernel import Kernel
from app.errors import CheckpointTooLargeError, RollbackTargetNotFoundError
from app.models import SimCheckpoint, TacticalEventLog, WargameSession
from app.state.checkpoint import (
    CheckpointManager,
    compute_state_hash,
    deserialize_state,
    recover,
    rollback,
    serialize_state,
)
from app.state.hot_state import InMemoryHotState, UnitState
from app.state.ledger import LedgerEvent, LedgerWriter

STATE_A: dict[str, UnitState] = {
    "u1": {"lat": 25.0, "lng": 121.5, "health": 100},
    "u2": {"lat": 24.0, "lng": 120.0, "health": 80},
}


@pytest.fixture
def session_id(session_factory: sessionmaker[Session]) -> str:
    with session_factory() as db:
        ws = WargameSession(name="ckpt", master_seed=1, current_weather={})
        db.add(ws)
        db.commit()
        return ws.id


# ---------------- serialize / hash ----------------


def test_serialize_deserialize_roundtrip() -> None:
    assert deserialize_state(serialize_state(STATE_A)) == STATE_A


def test_compression_reduces_size() -> None:
    big: dict[str, UnitState] = {f"u{i}": {"health": 100, "posture": "DEFEND"} for i in range(200)}
    assert len(serialize_state(big)) < len(str(big).encode())


def test_state_hash_deterministic() -> None:
    assert compute_state_hash(STATE_A) == compute_state_hash(dict(STATE_A))


def test_state_hash_key_order_independent() -> None:
    reordered: dict[str, UnitState] = {"u2": STATE_A["u2"], "u1": STATE_A["u1"]}
    assert compute_state_hash(STATE_A) == compute_state_hash(reordered)


def test_state_hash_changes_with_content() -> None:
    mutated = {**STATE_A, "u1": {**STATE_A["u1"], "health": 50}}
    assert compute_state_hash(STATE_A) != compute_state_hash(mutated)


# ---------------- CheckpointManager ----------------


def test_checkpoint_persists_and_loads(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=300, state=STATE_A, ledger_seq=42)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 300
    assert record.ledger_seq == 42
    assert record.state == STATE_A
    assert record.state_hash == compute_state_hash(STATE_A)


def test_load_latest_none_when_empty(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    assert CheckpointManager(session_factory).load_latest(session_id) is None


def test_load_latest_orders_by_ledger_seq_not_tick(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    # rollback 後新世代 tick 較小但 seq 較大——「最近」必須依 seq 判定（O1.7/R3）
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=300, state=STATE_A, ledger_seq=10)
    mgr.checkpoint(session_id, tick=50, state={"u1": {"health": 5}}, ledger_seq=99)
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.tick == 50
    assert record.ledger_seq == 99


def test_load_at_tick(session_factory: sessionmaker[Session], session_id: str) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, tick=100, state=STATE_A, ledger_seq=1)
    mgr.checkpoint(session_id, tick=300, state={"u1": {"health": 10}}, ledger_seq=2)
    record = mgr.load_at_tick(session_id, 100)
    assert record is not None
    assert record.tick == 100
    assert mgr.load_at_tick(session_id, 999) is None


def test_checkpoint_size_guard(
    session_factory: sessionmaker[Session], session_id: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(checkpoint_mod, "MAX_CHECKPOINT_BYTES", 1)
    with pytest.raises(CheckpointTooLargeError, match="超過上限"):
        CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A, ledger_seq=0)


def test_checkpoint_same_tick_overwrites(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    mgr.checkpoint(session_id, 300, STATE_A, ledger_seq=1)
    mgr.checkpoint(session_id, 300, {"u1": {"health": 1}}, ledger_seq=2)
    with session_factory() as db:
        rows = list(
            db.execute(
                select(SimCheckpoint).where(SimCheckpoint.session_id == session_id)
            ).scalars()
        )
    assert len(rows) == 1
    record = mgr.load_latest(session_id)
    assert record is not None
    assert record.state == {"u1": {"health": 1}}


# ---------------- recover ----------------


def test_recover_restores_hot_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    CheckpointManager(session_factory).checkpoint(session_id, 300, STATE_A, ledger_seq=7)
    hot = InMemoryHotState()  # 模擬崩潰後的空熱狀態
    result = recover(session_factory, session_id, hot)
    assert result.restored
    assert result.restored_tick == 300
    assert result.restored_ledger_seq == 7
    assert result.events_after_checkpoint == 0
    assert hot.get_all() == STATE_A


def test_recover_no_checkpoint(session_factory: sessionmaker[Session], session_id: str) -> None:
    result = recover(session_factory, session_id, InMemoryHotState())
    assert not result.restored
    assert result.restored_tick is None


def test_recover_counts_events_by_seq(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    writer = LedgerWriter(session_factory)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)])
    # checkpoint 錨定在 seq=2；之後兩筆事件「tick 很小」（模擬 rollback 後的新世代）
    CheckpointManager(session_factory).checkpoint(session_id, tick=2, state=STATE_A, ledger_seq=2)
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=0)])
    writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=1)])
    result = recover(session_factory, session_id, InMemoryHotState())
    # tick 比 checkpoint.tick 小，但 seq 在後 → 必須被算進 events_after（O1.7/R3）
    assert result.events_after_checkpoint == 2


def test_recover_invokes_transport_reset(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    CheckpointManager(session_factory).checkpoint(session_id, 1, STATE_A, ledger_seq=0)
    calls: list[bool] = []
    recover(
        session_factory, session_id, InMemoryHotState(), transport_reset=lambda: calls.append(True)
    )
    assert calls == [True]


def test_recovered_state_hash_matches_precrash() -> None:
    pre = InMemoryHotState()
    pre.put_unit("u1", {"lat": 25.0, "health": 100})
    pre.update_unit("u1", {"health": 60})
    snapshot = pre.get_all()
    pre_hash = compute_state_hash(snapshot)

    post = InMemoryHotState()
    post.restore(deserialize_state(serialize_state(snapshot)))
    assert compute_state_hash(post.get_all()) == pre_hash


# ---------------- rollback（O1.7/R1/R2 回歸） ----------------


def test_rollback_discards_later_checkpoints_so_recover_honors_rollback(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """R2 回歸：rollback 後 crash-recover 不得復活被回滾的狀態。"""
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=0)
    hot.update_unit("u1", {"health": 20})
    mgr.checkpoint(session_id, tick=5, state=hot.get_all(), ledger_seq=10)

    result = rollback(
        session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0
    )
    assert result.checkpoints_discarded == 1

    crashed = InMemoryHotState()
    recovered = recover(session_factory, session_id, crashed)
    assert recovered.restored_tick == 0
    assert crashed.get_all() == {"u1": {"health": 100}}  # 不是被回滾掉的 h=20


def test_rollback_writes_event_with_detail(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)
    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)
    with session_factory() as db:
        events = list(
            db.execute(
                select(TacticalEventLog).where(TacticalEventLog.session_id == session_id)
            ).scalars()
        )
    assert len(events) == 1
    assert events[0].event_type == "ROLLBACK"
    assert events[0].detail is not None
    assert events[0].detail["rolled_back_to"] == 0
    assert events[0].ai_decision == {}  # 診斷不再塞 aiDecision（O1.7/R8）


def test_rollback_unknown_tick_raises_domain_error(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=0, state=hot.get_all(), ledger_seq=0
    )
    with pytest.raises(RollbackTargetNotFoundError, match="無 tick=99"):
        rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=99)


def test_kernel_writer_survives_foreign_rollback(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """R1 回歸（review 實證重現的 bug）：另一 writer rollback 後，Kernel writer 續寫不撞 seq。"""
    kernel_writer = LedgerWriter(session_factory)
    kernel_writer.append(
        session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=t) for t in range(3)]
    )  # seq 0..2
    hot = InMemoryHotState()
    hot.put_unit("u1", {"h": 100})
    CheckpointManager(session_factory).checkpoint(
        session_id, tick=1, state=hot.get_all(), ledger_seq=2
    )
    rollback(
        session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=1
    )  # seq 3

    # 修復前：IntegrityError（重複 seq 3）。修復後：偵測衝突→重讀 tip→接 seq 4
    kernel_writer.append(session_id, [LedgerEvent(event_type="MOVEMENT_STEP", tick=2)])
    from app.state.ledger import verify_chain

    with session_factory() as db:
        rows = list(
            db.execute(
                select(TacticalEventLog)
                .where(TacticalEventLog.session_id == session_id)
                .order_by(TacticalEventLog.seq.asc())
            ).scalars()
        )
    assert [r.seq for r in rows] == [0, 1, 2, 3, 4]
    assert verify_chain(rows).ok


# ---------------- Kernel checkpoint cadence（fake checkpointer） ----------------


class CollectingCheckpointer:
    def __init__(self) -> None:
        self.saved: list[tuple[str, int, dict[str, UnitState], int]] = []

    def checkpoint(
        self, session_id: str, tick: int, state: Mapping[str, UnitState], ledger_seq: int
    ) -> None:
        self.saved.append((session_id, tick, {k: dict(v) for k, v in state.items()}, ledger_seq))


async def test_kernel_checkpoints_every_interval(build_noop_kernel: Callable[..., Kernel]) -> None:
    ckpt = CollectingCheckpointer()
    kernel = build_noop_kernel(checkpointer=ckpt, checkpoint_interval=3)
    await kernel.run(7)  # ticks 0..6
    assert [tick for _, tick, _, _ in ckpt.saved] == [0, 3, 6]


def test_kernel_rejects_invalid_checkpoint_interval(
    build_noop_kernel: Callable[..., Kernel],
) -> None:
    with pytest.raises(ValueError, match="checkpoint_interval"):
        build_noop_kernel(checkpoint_interval=0)


# ---------------- rollback 也要回捲 DB 單位列（Backlog 清理，WP-C10.5 發現） ----------------


def _seed_unit(factory: sessionmaker[Session], session_id: str) -> str:
    """建一個有座標/戰力的單位（DB 列），供回滾對帳。"""
    from app.models.enums import UnitLevel
    from app.models.tables import TacticalUnit, WargameSession

    with factory() as db:
        if db.get(WargameSession, session_id) is None:
            db.add(WargameSession(id=session_id, name="rb", master_seed=1, current_weather={}))
            db.flush()
        u = TacticalUnit(
            session_id=session_id,
            designation="U1",
            unit_level=UnitLevel.PLATOON,
            faction="BLUE",
            current_lat=23.0,
            current_lng=121.0,
            current_strength=100.0,
            authorized_strength=100.0,
            health_status=100.0,
        )
        db.add(u)
        db.commit()
        return u.id


def test_rollback_rewinds_the_db_unit_row_not_just_hot_state(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """**回滾看起來成功了，重啟一次就打回原形。**

    `hot_state.restore` 只還原熱狀態，而 `seed_combat_state` 每次 runner 啟動都
    **無條件**以 DB 的 current_lat/lng 覆蓋熱狀態——回滾不動 DB 列的話，
    單位會彈回回滾前的位置，而且畫面上在重啟前完全看不出來。
    """
    from app.models.tables import TacticalUnit

    uid = _seed_unit(session_factory, session_id)
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit(uid, {"lat": 23.0, "lng": 121.0, "strength": 100.0, "health": 100.0})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    # 之後單位移動且受損——活模擬會同時寫熱狀態與 DB 列。
    hot.update_unit(uid, {"lat": 23.5, "lng": 121.5, "strength": 40.0, "health": 40.0})
    with session_factory() as db:
        row = db.get(TacticalUnit, uid)
        assert row is not None
        row.current_lat, row.current_lng = 23.5, 121.5
        row.current_strength, row.health_status = 40.0, 40.0
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        row = db.get(TacticalUnit, uid)
        assert row is not None
        assert (row.current_lat, row.current_lng) == (23.0, 121.0), "DB 座標還停在回滾前"
        assert row.current_strength == 100.0, "DB 戰力還停在回滾前——GET /units 會顯示已被回滾的戰損"


def test_rollback_reports_how_many_unit_rows_it_rewound(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    uid = _seed_unit(session_factory, session_id)
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit(uid, {"lat": 23.0, "lng": 121.0})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)
    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)
    with session_factory() as db:
        ev = (
            db.execute(select(TacticalEventLog).where(TacticalEventLog.event_type == "ROLLBACK"))
            .scalars()
            .first()
        )
    assert ev is not None and ev.detail is not None
    assert ev.detail["units_restored"] == 1


def test_msel_memory_survives_a_checkpoint(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """**不還原的話重啟就重播狀況**——`once` 條目的「已觸發」若只活在記憶體，
    runner 一重建就全部重新武裝，D+2 的增援會在每次重啟時再來一次。"""
    from app.scenario.msel_runtime import MselMemory, MselRuntime
    from app.scenario.triggers import MselEntry, TriggerContext
    from app.state.checkpoint import restore_msel_memory

    entry = MselEntry(id="e1", trigger={"type": "time", "at_tick": 0}, inject={"event_type": "X"})
    rt = MselRuntime([entry], lambda t: TriggerContext(tick=t))
    rt.check(type("T", (), {"tick": 0})())
    assert rt.memory.fired_at == {"e1": 0}

    mgr = CheckpointManager(session_factory, extras_provider=lambda: {"msel": rt.memory.to_dict()})
    hot = InMemoryHotState()
    hot.put_unit("u1", {"health": 100})
    mgr.checkpoint(session_id, tick=3, state=hot.get_all(), ledger_seq=-1)

    # 模擬重啟：全新的 runtime（記憶是空的）
    fresh = MselRuntime([entry], lambda t: TriggerContext(tick=t), memory=MselMemory())
    record = mgr.load_latest(session_id)
    assert record is not None
    assert restore_msel_memory(record, fresh) is True
    assert fresh.check(type("T", (), {"tick": 9})()) == [], "重啟後 once 條目又觸發了一次"


# ---- 回滾涵蓋範圍：彈藥/油料、模擬生成的地圖物件、預劃目標 ----


def test_rollback_rewinds_ammo_and_fuel_in_the_db(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """熱狀態的彈藥由 `restore` 復原，**但 DB 那一份過去不會**。

    於是回滾後 `GET /units/{id}/weapons` 顯示的是回滾前打光的彈量，
    而補給與整備讀的也是 DB——兩份真相對不起來，且看得見的那一份是錯的。
    """
    from app.models.tables import EquipmentInstance, EquipmentTemplate

    uid = _seed_unit(session_factory, session_id)
    with session_factory() as db:
        tmpl = EquipmentTemplate(name="rifle", category="KINETIC", base_stats={})
        db.add(tmpl)
        db.flush()
        inst = EquipmentInstance(
            template_id=tmpl.id, owner_id=uid, quantity=1, current_state={"ammo": 200, "fuel": 90.0}
        )
        db.add(inst)
        db.commit()
        inst_id = inst.id

    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    hot.put_unit(uid, {"lat": 23.0, "lng": 121.0})
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    with session_factory() as db:  # 打了一場、跑了一段路
        row = db.get(EquipmentInstance, inst_id)
        assert row is not None
        row.current_state = {"ammo": 12, "fuel": 3.0}
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        row = db.get(EquipmentInstance, inst_id)
        assert row is not None
        assert row.current_state == {"ammo": 200, "fuel": 90.0}


def test_rollback_removes_obstacles_laid_after_the_target_tick(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """500 tick 埋的雷區，回滾到 300 之後如果還躺在那裡，那不是同一個戰場。"""
    from app.models.tables import MapFeature

    _seed_unit(session_factory, session_id)
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    with session_factory() as db:
        db.add(
            MapFeature(
                id="mine-1",
                session_id=session_id,
                kind="OBSTACLE",
                geometry_type="Point",
                geometry=[121.0, 23.0],
                owner_faction="BLUE",
                attributes={"obstacle_type": "MINEFIELD"},
            )
        )
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        assert db.get(MapFeature, "mine-1") is None


def test_rollback_revives_an_obstacle_that_was_breached_afterwards(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """開闢掉的障礙必須**復活**——所以是刪+建，不是逐列 update。"""
    from app.models.tables import MapFeature

    _seed_unit(session_factory, session_id)
    with session_factory() as db:
        db.add(
            MapFeature(
                id="wire-1",
                session_id=session_id,
                kind="OBSTACLE",
                geometry_type="Point",
                geometry=[121.0, 23.0],
                owner_faction="RED",
                attributes={"obstacle_type": "WIRE"},
            )
        )
        db.commit()

    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    with session_factory() as db:  # 工兵開闢後整列被刪掉
        db.delete(db.get(MapFeature, "wire-1"))
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        revived = db.get(MapFeature, "wire-1")
        assert revived is not None and revived.attributes["obstacle_type"] == "WIRE"


def test_rollback_leaves_hand_drawn_annotations_alone(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """玩家手繪的標繪是講評用的註記，不是世界狀態——回滾不該把它擦掉。"""
    from app.models.tables import MapFeature

    _seed_unit(session_factory, session_id)
    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    with session_factory() as db:
        db.add(
            MapFeature(
                id="note-1",
                session_id=session_id,
                kind="CONTROL_MEASURE",
                geometry_type="Point",
                geometry=[121.0, 23.0],
                owner_faction="BLUE",
                attributes={},
            )
        )
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        assert db.get(MapFeature, "note-1") is not None


def test_rollback_rearms_a_fire_plan_target_that_already_fired(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """回到射擊之前那個 tick，已 FIRED 的目標若仍是 FIRED，**那發準備射擊再也不會落下**。"""
    from app.models.enums import FirePlanTargetStatus
    from app.models.tables import FirePlan, FirePlanTarget

    uid = _seed_unit(session_factory, session_id)
    with session_factory() as db:
        plan = FirePlan(id="fp-1", session_id=session_id, faction="BLUE", name="AGM")
        db.add(plan)
        db.add(
            FirePlanTarget(
                id="tgt-1",
                plan_id="fp-1",
                seq=1,
                target_lat=23.1,
                target_lng=121.1,
                shooter_unit_id=uid,
                status=FirePlanTargetStatus.PENDING,
            )
        )
        db.commit()

    mgr = CheckpointManager(session_factory)
    hot = InMemoryHotState()
    mgr.checkpoint(session_id, tick=0, state=hot.get_all(), ledger_seq=-1)

    with session_factory() as db:
        db.get(FirePlanTarget, "tgt-1").status = FirePlanTargetStatus.FIRED  # type: ignore[union-attr]
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        assert db.get(FirePlanTarget, "tgt-1").status is FirePlanTargetStatus.PENDING  # type: ignore[union-attr]


def test_an_old_snapshot_without_the_new_sections_touches_nothing(
    session_factory: sessionmaker[Session], session_id: str
) -> None:
    """舊快照沒有這三段 → **一件都不動**。

    把每件裝備清成空狀態、把地圖物件全刪光，比不還原糟得多。
    """
    from app.models.tables import EquipmentInstance, EquipmentTemplate, MapFeature

    uid = _seed_unit(session_factory, session_id)
    with session_factory() as db:
        tmpl = EquipmentTemplate(name="r", category="KINETIC", base_stats={})
        db.add(tmpl)
        db.flush()
        inst = EquipmentInstance(
            template_id=tmpl.id, owner_id=uid, quantity=1, current_state={"ammo": 50}
        )
        db.add(inst)
        db.add(
            MapFeature(
                id="old-mine",
                session_id=session_id,
                kind="OBSTACLE",
                geometry_type="Point",
                geometry=[121.0, 23.0],
                owner_faction="RED",
                attributes={},
            )
        )
        db.commit()
        inst_id = inst.id

    # v1 裸 units map（沒有 extras）——正是升級前留下的那些快照。
    hot = InMemoryHotState()
    hot.put_unit(uid, {"lat": 23.0, "lng": 121.0})
    with session_factory() as db:
        db.add(
            SimCheckpoint(
                session_id=session_id,
                tick=0,
                ledger_seq=-1,
                state_blob=serialize_state(hot.get_all()),
                state_hash=compute_state_hash(hot.get_all()),
            )
        )
        db.commit()

    rollback(session_factory, LedgerWriter(session_factory), session_id, hot, target_tick=0)

    with session_factory() as db:
        assert db.get(EquipmentInstance, inst_id).current_state == {"ammo": 50}  # type: ignore[union-attr]
        assert db.get(MapFeature, "old-mine") is not None
