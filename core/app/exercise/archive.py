"""撤收建檔（WP-B1b）——演習資料的歸檔封包與銷毀。

## 為什麼是單一 JSON 信封而不是 zip

repo 裡**完全沒有任何 zip/stream/attachment 機制**（`zipfile`/`tarfile`/`gzip`/
`StreamingResponse`/`FileResponse`/`Content-Disposition` 在 core 與 ops 底下一個都搜不到），
而前端的下載路徑（`useAar.ts` 的 `aarExportDownload`）走 `apiFetch` → 它會 **parse body**，
拿二進位會直接壞掉。為了一張卡引進一整套二進位下載管線，代價遠大於它買到的東西。

單一 JSON 信封：後端一個 dict、前端一個 `Blob`，兩邊都不必新增機制。

## 帳本用「原始」讀法，AAR 用「投影」讀法——這兩者不可混用

- **歸檔的帳本**要 `TacticalEventLog` 依 seq 的**原樣**：那是證據，`verify_chain` 也要求
  seq 從 0 連續。用 `aar/events.read_events` 會少掉被回滾棄置的世代（ADR 007 邏輯截斷），
  於是產生一條 `verify_chain` **必定拒絕**的鏈——歸檔出一份驗不過的證據是最糟的結果。
- **AAR 統計**要 `read_events` 的投影：那才是「這場演習實際發生過什麼」的時間軸。

同一張表的兩種讀法，各有各的正確性。合併成一個「順便」會兩邊都錯。

## 歸檔封包是 ground truth

不套 `aar/fog.py` 的陣營投影——歸檔的定義就是完整證據。因此**只給全知**，
且不在既有的 `/aar/export` 上加 `raw=true` 之類的旗標（那會是繞過，紅線 3 的精神）。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aar.events import read_events
from app.aar.stats import compute_metrics
from app.models import (
    Exercise,
    ExerciseAuditLog,
    Scenario,
    SessionParticipant,
    TacticalEventLog,
    TacticalUnit,
    WargameSession,
)
from app.state.ledger import canonical_json, verify_chain

BUNDLE_VERSION = "1.0"

# 與 `exercise/service.py` 的常數同值。**刻意在此重宣告而不 import**：
# service 會 import 本模組（`build_bundle`），反向 import 就成環。
ACTION_BUNDLE_EXPORTED = "BUNDLE_EXPORTED"


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _ledger_rows(db: Session, session_id: str) -> list[TacticalEventLog]:
    """帳本原樣（依 seq）。**不做 ADR 007 的邏輯截斷**——見模組說明。"""
    return list(
        db.execute(
            select(TacticalEventLog)
            .where(TacticalEventLog.session_id == session_id)
            .order_by(TacticalEventLog.seq)
        )
        .scalars()
        .all()
    )


def _ledger_payload(row: TacticalEventLog) -> dict[str, Any]:
    return {
        "seq": row.seq,
        "tick": row.tick,
        "timestamp": _iso(row.timestamp),
        "event_type": row.event_type,
        "initiator_id": row.initiator_id,
        "target_id": row.target_id,
        "terrain_modifier": row.terrain_modifier,
        "damage_calc": row.damage_calc,
        "ai_decision": row.ai_decision,
        "reasoning_chain": row.reasoning_chain,
        "detail": row.detail,
        "prev_hash": row.prev_hash,
        "self_hash": row.self_hash,
    }


def _decode_package(blob: bytes | None) -> Any:
    try:
        return json.loads(bytes(blob or b"").decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        return {"decode_error": str(exc)}


def _session_bundle(db: Session, session: WargameSession) -> dict[str, Any]:
    rows = _ledger_rows(db, session.id)
    verdict = verify_chain(rows)
    units = (
        db.execute(select(TacticalUnit).where(TacticalUnit.session_id == session.id))
        .scalars()
        .all()
    )
    faction_of = {u.id: u.faction for u in units}
    metrics = compute_metrics(read_events(db, session.id), faction_of)
    participants = (
        db.execute(select(SessionParticipant).where(SessionParticipant.session_id == session.id))
        .scalars()
        .all()
    )
    return {
        "id": session.id,
        "name": session.name,
        "session_role": session.session_role.value if session.session_role else None,
        "scenario_id": session.scenario_id,
        "master_seed": int(session.master_seed),
        "mode": session.mode.value,
        "start_time": _iso(session.start_time),
        "archived_at": _iso(session.archived_at),
        "world_start_time": _iso(session.world_start_time),
        # 想定衍生的每局設定——歸檔要能重建「這一局是在什麼規則下跑的」。
        "faction_relations": session.faction_relations,
        "no_strike_zones": session.no_strike_zones,
        "roe": session.roe,
        "mobility_overrides": session.mobility_overrides,
        "survivability_move": session.survivability_move,
        "msel": session.msel,
        "request_quotas": session.request_quotas,
        "indirect_fire_requires_approval": session.indirect_fire_requires_approval,
        "participants": [
            {
                "user_id": p.user_id,
                "faction": p.faction,
                "role": p.role.value if hasattr(p.role, "value") else str(p.role),
                "seat_role": p.seat_role.value if p.seat_role else None,
            }
            for p in participants
        ],
        "units": [
            {
                "id": u.id,
                "designation": u.designation,
                "faction": u.faction,
                "unit_level": u.unit_level.value,
                "authorized_strength": u.authorized_strength,
                "current_strength": u.current_strength,
                "lat": u.current_lat,
                "lng": u.current_lng,
            }
            for u in units
        ],
        # 帳本原樣 + 鏈驗結果。**驗證結果一起歸檔**：事後有人問「這份資料可信嗎」，
        # 答案要在封包裡，不是要對方自己再跑一次工具。
        "ledger": {
            "count": len(rows),
            "verified": verdict.ok,
            "verified_count": verdict.verified_count,
            "break_seq": verdict.break_seq,
            "reason": verdict.reason,
            "events": [_ledger_payload(r) for r in rows],
        },
        "aar_metrics": {
            "total_events": metrics.total_events,
            "event_counts": metrics.event_counts,
            "engagements": metrics.engagements,
            "hits": metrics.hits,
            "hit_rate": metrics.hit_rate,
            "total_damage": metrics.total_damage,
            "guardrail_blocks": metrics.guardrail_blocks,
            "damage_by_faction": metrics.damage_by_faction,
            "max_tick": metrics.max_tick,
        },
    }


def build_bundle(db: Session, exercise: Exercise) -> dict[str, Any]:
    """組出整場演習的歸檔封包（AAR 統計 + 帳本原樣 + 想定包 + 稽核軌跡）。

    `content_hash` 由信封**除自己以外**的內容以 canonical JSON 雜湊而得：
    歸檔之後有人動過內容，比對得出來。
    """
    sessions = list(
        db.execute(
            select(WargameSession)
            .where(WargameSession.exercise_id == exercise.id)
            .order_by(WargameSession.start_time, WargameSession.id)
        )
        .scalars()
        .all()
    )
    scenario_ids = sorted({s.scenario_id for s in sessions if s.scenario_id})
    scenarios = []
    for sid in scenario_ids:
        row = db.get(Scenario, sid)
        if row is None:
            # 想定可被獨立刪除（`scenario_id` 無 FK）。**明說它不在了**——
            # 靜靜省略會讓歸檔看起來完整，實際上少了「這場演習照什麼想定跑的」。
            scenarios.append({"id": sid, "missing": True})
            continue
        scenarios.append(
            {
                "id": sid,
                "name": row.name,
                "version": row.version,
                "checksum": row.checksum,
                # `package_blob` 是 LargeBinary（存的是 UTF-8 JSON）。**解成 dict 才進信封**——
                # bytes 序列化不了 JSON，而 base64 會讓歸檔的人得多一道手續才讀得到內容。
                # 解不開就明說（壞掉的想定包也是歸檔要留的事實，不該讓整個封包炸掉）。
                "package": _decode_package(row.package_blob),
            }
        )
    # **匯出紀錄不進封包**。匯出本身會寫一筆稽核，若它也算進內容，
    # 每匯出一次下一次的 `content_hash` 就變一次——雜湊於是失去「有沒有被動過」的比對能力，
    # 而那正是它存在的唯一理由。封包記錄的是**這場演習怎麼進行的**；
    # 「誰在什麼時候把資料帶走」屬存取紀錄，留在 `/exercises/{id}/audit`（不會被帶走）。
    audit = (
        db.execute(
            select(ExerciseAuditLog)
            .where(
                ExerciseAuditLog.exercise_id == exercise.id,
                ExerciseAuditLog.action != ACTION_BUNDLE_EXPORTED,
            )
            .order_by(ExerciseAuditLog.seq)
        )
        .scalars()
        .all()
    )
    body: dict[str, Any] = {
        "bundle_version": BUNDLE_VERSION,
        "exercise": {
            "id": exercise.id,
            "name": exercise.name,
            "phase": exercise.phase.value,
            "created_by": exercise.created_by,
            "created_at": _iso(exercise.created_at),
            "schedule": exercise.schedule_json or {},
            "checklist": exercise.checklist_json or [],
        },
        "audit": [
            {
                "seq": a.seq,
                "at": _iso(a.at),
                "actor_id": a.actor_id,
                "action": a.action,
                "from_phase": a.from_phase.value if a.from_phase else None,
                "to_phase": a.to_phase.value if a.to_phase else None,
                "detail": a.detail or {},
            }
            for a in audit
        ],
        "scenarios": scenarios,
        "sessions": [_session_bundle(db, s) for s in sessions],
    }
    body["content_hash"] = hashlib.sha256(canonical_json(body).encode("utf-8")).hexdigest()
    return body


__all__ = ["BUNDLE_VERSION", "build_bundle"]
