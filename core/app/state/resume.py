"""Runner 重啟時把執行期狀態接回去（WP-E1；SPEC_V2 §6 WP-E1）。

`sim_runtime` 的 runner 會在多種情況下重建：core 重啟、崩潰、`session_restart_key`
（改自主 AI 指派）、rollback。**在此模組之前，每次重建都讓該局的 `SimClock` 回到 tick 0**
——Ledger 的 tick 倒退、`issued_at_tick` 歸零（指令排序壞）、comms 延遲閘門的 now_tick 倒退、
victory 的 time 條件永遠不到。本模組提供「續接點」的單一判定。

## 起始 tick 的來源優先序（且刻意不含 Ledger）

1. Redis `session:{id}:tick`——broadcaster 每個有活動的 tick 寫一次，且寫在該 tick 的
   Ledger 批次**提交之後**（kernel: append → publish_events → publish → checkpoint → advance）。
   core 崩潰但 Redis 存活時這是最新的。
2. 最近一次 checkpoint 的 tick（Redis 也沒了時）。
3. 都沒有 → 0（全新的局）。

兩者都代表「該 tick 已跑完」，故起始 tick ＝ 來源 + 1。

**為什麼不看 Ledger 的最大 tick**：rollback 後 tick 非單調（被棄世代的事件 tick 更大），
拿最大值會直接把白軍的回滾抵銷掉。單調的是 seq、不是 tick——這與 `checkpoint.py`
「ledgerSeq 才是時間軸身分」的既有結論一致。

**已知的重跑窗**：若崩潰發生在「Ledger 批次已寫、tick 鍵未更新」之間，該 tick 會重跑一次
（事件重複入帳，seq 續增）。已 drain 的指令狀態早已 commit 進 DB，不會被重跑第二次；
真正會重複的是移動步進。這是 at-least-once 的代價，比漏跑安全。
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import TacticalEventLog
from app.state.checkpoint import CheckpointManager, CheckpointRecord
from app.state.hot_state import HotStateStore, UnitState, session_tick_key

_LOG = logging.getLogger("app.resume")

# 前滾能重建的欄位，逐事件型別列表。**只列事件確實帶著結果值者**——推測性的映射
# 會安靜地寫入錯的狀態，比不前滾更糟。
_POSITION_EVENTS = frozenset({"UNIT_MOVED", "UNIT_ARRIVED", "MOVE_HALTED_FUEL"})
_ENGAGEMENT_EVENT = "ENGAGEMENT_RESOLVED"


def read_live_tick(client: Any, session_id: str) -> int | None:
    """讀 Redis 的當前 sim tick；無鍵/壞值 → None（與「tick 0」區別開來）。"""
    try:
        raw = client.get(session_tick_key(session_id))
    except Exception:  # Redis 掛掉不該擋住 runner 啟動
        return None
    if raw is None:
        return None
    try:
        return int(raw)
    except (ValueError, TypeError):
        return None


def resume_tick(session_factory: Any, client: Any, session_id: str) -> int:
    """runner (重)啟動時的起始 tick（見模組 docstring 的來源優先序）。"""
    live = read_live_tick(client, session_id)
    if live is not None:
        return live + 1
    record = CheckpointManager(session_factory).load_latest(session_id)
    if record is not None:
        _LOG.info("session %s 無 Redis tick，自 checkpoint tick=%d 續跑", session_id, record.tick)
        return record.tick + 1
    return 0


def _event_patch(row: TacticalEventLog) -> tuple[str | None, UnitState]:
    """單一 Ledger 事件 → (被影響的 unit_id, 熱狀態補丁)。無可重建者回 (None, {})。"""
    if row.event_type in _POSITION_EVENTS:
        detail = row.detail or {}
        lat, lng = detail.get("lat"), detail.get("lng")
        if row.initiator_id is None or lat is None or lng is None:
            return None, {}
        patch: UnitState = {"lat": float(lat), "lng": float(lng)}
        if row.event_type == "MOVE_HALTED_FUEL":
            patch["fuel"] = 0.0  # 移動系統於斷油時把熱狀態的 fuel 歸零（engine/movement）
        return row.initiator_id, patch
    if row.event_type == _ENGAGEMENT_EVENT:
        decision = row.ai_decision or {}
        if row.target_id is None:
            return None, {}
        patch = {}
        if "target_health_after" in decision:
            patch["health"] = float(decision["target_health_after"])
        if decision.get("target_strength_after") is not None:
            patch["strength"] = float(decision["target_strength_after"])
        return (row.target_id, patch) if patch else (None, {})
    return None, {}


def forward_roll(
    session_factory: sessionmaker[Session], session_id: str, hot: HotStateStore, after_seq: int
) -> int:
    """把 checkpoint 之後的 Ledger 尾段投影回熱狀態；回傳套用的事件數。

    **這不是確定性重播**——Kernel 的輸入（DB 指令、感測掃描、AI 決策）沒有被錄下來，
    沒有東西可以「重跑」。能做的是另一件事：Ledger 事件本身就記著**結果值**
    （移動事件帶 lat/lng、交戰事件帶 target_health_after/target_strength_after），
    照 seq 依序套用即可把熱狀態推回崩潰當下。這是投影（projection），不是 replay。

    **重建不到的東西**（有意識的取捨，非疏漏）：
    - 逐 tick 的油料消耗（`engine/movement` 每 tick 更新熱狀態 `fuel` 但不發事件）——
      只能回到快照值，直到下一次補給或斷油事件校正。
    - 彈藥（交戰事件不帶扣後餘量）——但它持久化在 DB EquipmentInstance（#53），
      `seed_combat_state` 會在熱狀態缺鍵時補回，故實際不會遺失。
    - `comms_state`（純推導欄位，`CommsSystem` 下一個重算週期即回復）。

    依 seq 排序（非 tick）：rollback 後 tick 非單調，seq 才是帳本的時間軸身分。
    """
    applied = 0
    with session_factory() as db:
        rows = db.scalars(
            select(TacticalEventLog)
            .where(
                TacticalEventLog.session_id == session_id,
                TacticalEventLog.seq > after_seq,
            )
            .order_by(TacticalEventLog.seq.asc())
        ).all()
    for row in rows:
        unit_id, patch = _event_patch(row)
        if unit_id and patch:
            hot.update_unit(unit_id, patch)
            applied += 1
    return applied


@dataclass(frozen=True, slots=True)
class ResumeResult:
    start_tick: int
    restored_from_checkpoint: bool
    restored_tick: int | None
    rng_streams_restored: int
    forward_rolled_events: int


def restore_rng(record: CheckpointRecord, rngs: Mapping[str, Any]) -> int:
    """把快照裡的各 stream 位置灌回 RNG 實例；回傳成功還原的 stream 數。

    RNG 只活在記憶體裡，**任何**重啟都會失去它——這與 Redis 是否存活無關。不還原的話，
    重啟後整局會重播一段已經用過的隨機序列（同種子同結果），交戰命中判定會出現
    「重啟就重演」的可疑規律。
    """
    saved = record.rng_states()
    restored = 0
    for stream_id, rng in rngs.items():
        state = saved.get(stream_id)
        if not isinstance(state, dict):
            continue
        try:
            rng.set_state(state)
            restored += 1
        except ValueError:
            _LOG.warning("RNG stream %s 的快照狀態不合法，該 stream 自種子重新開始", stream_id)
    return restored


def resume_session(
    *,
    session_factory: sessionmaker[Session],
    client: Any,
    session_id: str,
    hot: HotStateStore,
    rngs: Mapping[str, Any] | None = None,
    transport_reset: Any = None,
) -> ResumeResult:
    """runner 啟動前把執行期狀態接回去（WP-E1 (4)）。

    **只有熱狀態是空的（Redis 沒了）才從快照還原**：core 崩潰但 Redis 存活時，熱狀態
    比任何快照都新，硬套快照等於把進度倒退回上一個間隔。RNG 則相反——它一定要還原。
    """
    record = CheckpointManager(session_factory).load_latest(session_id)
    start = resume_tick(session_factory, client, session_id)
    if record is None:
        return ResumeResult(start, False, None, 0, 0)

    rng_restored = restore_rng(record, rngs or {})
    if hot.get_all():
        return ResumeResult(start, False, None, rng_restored, 0)

    hot.restore(record.state)
    if transport_reset is not None:
        transport_reset()
    rolled = forward_roll(session_factory, session_id, hot, record.ledger_seq)
    _LOG.warning(
        "session %s 熱狀態已遺失，自 checkpoint tick=%d 復原（前滾 %d 則事件，RNG %d 條）",
        session_id,
        record.tick,
        rolled,
        rng_restored,
    )
    return ResumeResult(start, True, record.tick, rng_restored, rolled)
