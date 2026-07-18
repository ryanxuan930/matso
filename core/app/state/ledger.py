"""Event Ledger 寫入器與 tamper-evident hash chain（SPEC_FULL §15.3）。

TacticalEventLog 為 append-only：
- 應用層防線：本模組只提供 append，**不提供任何 update/delete**。
- DB 權限層防線：ops/tools/grant_ledger_readonly.sql 對 app 帳號 revoke UPDATE/DELETE。

每個事件帶：
- `seq`：session 內單調遞增（從 0 起），由寫入者發號（O1.3 起改由 Kernel 持有 writer 發號）。
- `prevHash` / `selfHash`：`selfHash = SHA256(prevHash ‖ canonical_json(payload))`，
  形成鏈式雜湊。任一事件被竄改，其 selfHash 與後續所有 prevHash 皆對不上 → 可偵測。

**可重現性（P4）關鍵**：hash 只涵蓋決定性欄位，排除 id（隨機 uuid）與 timestamp（牆鐘），
故 golden replay 以相同指令序列重跑會得到相同的 hash chain。
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from app.models import TacticalEventLog

# 鏈的起點：第一個事件的 prevHash。
GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> str:
    """決定性 JSON 序列化：鍵排序、無多餘空白、保留非 ASCII。

    鍵順序不影響輸出，因此同一語意的 payload 永遠得到相同字串與相同 hash。
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_event_payload(
    *,
    session_id: str,
    seq: int,
    tick: int,
    event_type: str,
    initiator_id: str | None,
    target_id: str | None,
    weather_snapshot: Any,
    terrain_modifier: float,
    reasoning_chain: str | None,
    ai_decision: Any,
    damage_calc: float | None,
) -> dict[str, Any]:
    """建構進入 hash 的決定性欄位集合。

    ⚠ 寫入（LedgerWriter）與驗證（verify_chain）都必須經由本函式，兩者一致才能自我驗證。
    刻意排除 id 與 timestamp（非決定性），確保 golden replay 可重現。
    """
    return {
        "sessionId": session_id,
        "seq": seq,
        "tick": tick,
        "eventType": event_type,
        "initiatorId": initiator_id,
        "targetId": target_id,
        "weatherSnapshot": weather_snapshot,
        "terrainModifier": terrain_modifier,
        "reasoningChain": reasoning_chain,
        "aiDecision": ai_decision,
        "damageCalc": damage_calc,
    }


def compute_self_hash(prev_hash: str, payload: dict[str, Any]) -> str:
    """selfHash = SHA256(prevHash ‖ canonical_json(payload))。"""
    material = prev_hash + canonical_json(payload)
    return hashlib.sha256(material.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class LedgerEvent:
    """寫入帳本的事件輸入（決定性內容；seq/hash/id/timestamp 由寫入器與 DB 補上）。

    eventType 值域見 SPEC_FULL §15.3 枚舉。
    """

    event_type: str
    tick: int
    initiator_id: str | None = None
    target_id: str | None = None
    weather_snapshot: dict[str, Any] = field(default_factory=dict)
    terrain_modifier: float = 0.0
    reasoning_chain: str | None = None
    ai_decision: dict[str, Any] = field(default_factory=dict)
    damage_calc: float | None = None


class LedgerWriter:
    """帳本寫入器。**唯一對外能力是 append**——刻意不提供 update/delete。

    single-writer 前提（Kernel 為唯一寫入者，SPEC_FULL §3.4）下，以記憶體快取每個
    session 的鏈尾 (last_seq, last_hash)；首次寫入某 session 時由 DB 查最大 seq 初始化，
    支援跨行程重啟接續（DB 為權威）。
    """

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory
        self._tips: dict[str, tuple[int, str]] = {}

    def append(self, session_id: str, events: Sequence[LedgerEvent]) -> list[str]:
        """批次寫入事件，回傳各事件的 selfHash（依序）。空輸入回空清單。"""
        if not events:
            return []
        with self._session_factory() as db:
            last_seq, prev_hash = self._tip(db, session_id)
            rows: list[TacticalEventLog] = []
            hashes: list[str] = []
            seq = last_seq
            for ev in events:
                seq += 1
                payload = canonical_event_payload(
                    session_id=session_id,
                    seq=seq,
                    tick=ev.tick,
                    event_type=ev.event_type,
                    initiator_id=ev.initiator_id,
                    target_id=ev.target_id,
                    weather_snapshot=ev.weather_snapshot,
                    terrain_modifier=ev.terrain_modifier,
                    reasoning_chain=ev.reasoning_chain,
                    ai_decision=ev.ai_decision,
                    damage_calc=ev.damage_calc,
                )
                self_hash = compute_self_hash(prev_hash, payload)
                rows.append(
                    TacticalEventLog(
                        session_id=session_id,
                        seq=seq,
                        tick=ev.tick,
                        event_type=ev.event_type,
                        initiator_id=ev.initiator_id,
                        target_id=ev.target_id,
                        weather_snapshot=ev.weather_snapshot,
                        terrain_modifier=ev.terrain_modifier,
                        reasoning_chain=ev.reasoning_chain,
                        ai_decision=ev.ai_decision,
                        damage_calc=ev.damage_calc,
                        prev_hash=prev_hash,
                        self_hash=self_hash,
                    )
                )
                prev_hash = self_hash
                hashes.append(self_hash)
            db.add_all(rows)
            db.commit()
        self._tips[session_id] = (seq, prev_hash)
        return hashes

    def _tip(self, db: Session, session_id: str) -> tuple[int, str]:
        """回傳 (last_seq, last_hash)；空 session 回 (-1, GENESIS_HASH)（首事件 seq=0）。"""
        cached = self._tips.get(session_id)
        if cached is not None:
            return cached
        stmt = (
            select(TacticalEventLog.seq, TacticalEventLog.self_hash)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq.desc())
            .limit(1)
        )
        row = db.execute(stmt).first()
        if row is None:
            return (-1, GENESIS_HASH)
        return (int(row[0]), str(row[1]))


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """hash chain 驗證結果。ok=True 時 break_seq/reason 為 None。"""

    ok: bool
    verified_count: int
    break_seq: int | None = None
    reason: str | None = None


def verify_chain(events: Iterable[TacticalEventLog]) -> VerifyResult:
    """依 seq 順序重算整條鏈，回報第一個斷點。

    events MUST 已依 seq 升冪排序。檢查三件事：
    1. seq 從 0 起連續遞增（缺號 = 被刪）。
    2. 每筆 prevHash 等於前一筆的 selfHash（鏈接正確）。
    3. 每筆 selfHash 等於以其決定性欄位重算的值（內容未被竄改）。
    """
    prev_hash = GENESIS_HASH
    verified = 0
    for expected_seq, ev in enumerate(events):
        if ev.seq != expected_seq:
            return VerifyResult(
                ok=False,
                verified_count=verified,
                break_seq=ev.seq,
                reason=f"seq 不連續：預期 {expected_seq}，實際 {ev.seq}（疑似刪除或亂序）",
            )
        if ev.prev_hash != prev_hash:
            return VerifyResult(
                ok=False,
                verified_count=verified,
                break_seq=ev.seq,
                reason=f"prevHash 對不上：預期 {prev_hash}，實際 {ev.prev_hash}",
            )
        payload = canonical_event_payload(
            session_id=ev.session_id,
            seq=ev.seq,
            tick=ev.tick,
            event_type=ev.event_type,
            initiator_id=ev.initiator_id,
            target_id=ev.target_id,
            weather_snapshot=ev.weather_snapshot,
            terrain_modifier=ev.terrain_modifier,
            reasoning_chain=ev.reasoning_chain,
            ai_decision=ev.ai_decision,
            damage_calc=ev.damage_calc,
        )
        recomputed = compute_self_hash(prev_hash, payload)
        if ev.self_hash != recomputed:
            return VerifyResult(
                ok=False,
                verified_count=verified,
                break_seq=ev.seq,
                reason=f"selfHash 對不上：內容被竄改（seq={ev.seq}）",
            )
        prev_hash = ev.self_hash
        verified += 1
    return VerifyResult(ok=True, verified_count=verified)
