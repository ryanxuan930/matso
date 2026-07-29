"""演習服務（WP-B1）——建演習、推階段、勾稽、掛/卸 session、稽核軌跡。

## 誰看得到

**演習層是導演工具**，故 `list/get` 限全知（統裁/白軍/管理），`mutate` 限白軍（統裁/白軍幕僚）。
ADMIN 是系統管理而非統裁（`stream/faction_filter` 的既有裁示），故看得到但推不動階段。

作戰方看到的是自己那一局——`SessionSummary.exercise_id` / `session_role` 是每局資訊，人人可見。
把整個演習物件也開給作戰方，等於把「還有兩場預推、正式局排在 D-day」這種導演資訊送出去。

## 為什麼稽核軌跡另開一張表

`TacticalEventLog` 是 golden 會驗的雜湊鏈。階段推進是**牆鐘的、人為的、局外的**事件，
寫進鏈裡會擾動決定性重播——SPEC 說「專屬 audit 表」正是為此。
"""

from __future__ import annotations

import contextlib
import datetime as _dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.errors import (
    AuthForbiddenError,
    ExerciseChecklistIncompleteError,
    ExerciseDestroyConfirmError,
    ExerciseNotFoundError,
    ExercisePhaseInvalidError,
    ExerciseSessionConflictError,
)
from app.exercise.archive import ACTION_BUNDLE_EXPORTED, build_bundle
from app.exercise.phases import default_checklist, is_valid_transition, missing_required
from app.exercise.schemas import (
    AdvancePhaseRequest,
    AttachSessionRequest,
    CreateExerciseRequest,
    ExerciseAuditEntry,
    ExerciseChecklistItem,
    ExerciseSessionRef,
    ExerciseView,
    SealView,
)
from app.governance.seal import build_seal_payload, compress, compute_seal_hash, seal_for
from app.lobby.purge import purge_session_redis, purge_session_rows
from app.models import (
    Exercise,
    ExerciseAuditLog,
    ExercisePhase,
    ParameterSeal,
    UserRole,
    WargameSession,
)
from app.stream.faction_filter import is_omniscient, is_white_cell

# 稽核 action 常數——字串散在各處會拼錯，而稽核軌跡拼錯了沒有人會發現。
ACTION_CREATED = "EXERCISE_CREATED"
ACTION_PHASE_ADVANCED = "PHASE_ADVANCED"
ACTION_CHECKLIST_TICKED = "CHECKLIST_TICKED"
ACTION_SESSION_ATTACHED = "SESSION_ATTACHED"
ACTION_SESSION_DETACHED = "SESSION_DETACHED"
ACTION_DESTROYED = "DATA_DESTROYED"
ACTION_SEALED = "PARAMS_SEALED"
ACTION_UNSEALED = "PARAMS_UNSEALED"


def _now() -> _dt.datetime:
    """真實牆鐘。**這不是模擬時間**——演習階段是局外的人為事件，
    與 SimClock 無關（紅線 1 管的是模擬邏輯）。"""
    return _dt.datetime.now()


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


class ExerciseService:
    def __init__(self, db: Session) -> None:
        self._db = db

    # ---- 讀 ----

    def list_exercises(self, user: CurrentUser) -> list[ExerciseView]:
        self._require_view(user)
        rows = self._db.execute(select(Exercise).order_by(Exercise.created_at)).scalars().all()
        return [self._view(e) for e in rows]

    def get_exercise(self, user: CurrentUser, exercise_id: str) -> ExerciseView:
        self._require_view(user)
        return self._view(self._get(exercise_id))

    def get_audit(self, user: CurrentUser, exercise_id: str) -> list[ExerciseAuditEntry]:
        self._require_view(user)
        self._get(exercise_id)  # 不存在 → 404（不要回空 list 假裝有這個演習）
        rows = (
            self._db.execute(
                select(ExerciseAuditLog)
                .where(ExerciseAuditLog.exercise_id == exercise_id)
                .order_by(ExerciseAuditLog.seq)
            )
            .scalars()
            .all()
        )
        return [
            ExerciseAuditEntry(
                id=r.id,
                at=_iso(r.at) or "",
                actor_id=r.actor_id,
                action=r.action,
                from_phase=r.from_phase,
                to_phase=r.to_phase,
                detail=dict(r.detail or {}),
            )
            for r in rows
        ]

    # ---- 寫 ----

    def create_exercise(self, user: CurrentUser, req: CreateExerciseRequest) -> ExerciseView:
        self._require_white_cell(user)
        exercise = Exercise(
            name=req.name,
            phase=ExercisePhase.PREP,
            schedule_json=dict(req.schedule),
            checklist_json=default_checklist(),
            created_by=user.id,
        )
        self._db.add(exercise)
        self._db.flush()
        self._audit(exercise.id, user.id, ACTION_CREATED, to_phase=ExercisePhase.PREP)
        self._db.commit()
        return self._view(exercise)

    def delete_exercise(self, user: CurrentUser, exercise_id: str) -> None:
        """刪演習專案本身。**不動任何 session**——掛在底下的局改回獨立局。

        銷毀 session 資料是完全另一回事（WP-B1b 的銷毀模式）。把兩者綁在一起，
        「我按錯了想刪掉這個空專案」就會變成刪掉整場演習的資料。
        """
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        for s in self._sessions_of(exercise_id):
            s.exercise_id = None
            s.session_role = None
        self._db.delete(exercise)  # audit 由 FK ON DELETE CASCADE 帶走
        self._db.commit()

    def advance_phase(
        self, user: CurrentUser, exercise_id: str, req: AdvancePhaseRequest
    ) -> ExerciseView:
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        current = exercise.phase
        if not is_valid_transition(current, req.phase):
            raise ExercisePhaseInvalidError(
                f"{current.value} → {req.phase.value} 非法：只能沿序前進、一次一階",
                details={"from": current.value, "to": req.phase.value},
            )
        missing = missing_required(exercise.checklist_json, current)
        if missing:
            raise ExerciseChecklistIncompleteError(
                f"離開 {current.value} 前還有 {len(missing)} 項必要整備未完成",
                details={"missing": missing, "phase": current.value},
            )
        exercise.phase = req.phase
        exercise.phase_changed_at = _now()
        self._audit(
            exercise_id,
            user.id,
            ACTION_PHASE_ADVANCED,
            from_phase=current,
            to_phase=req.phase,
            detail={"note": req.note} if req.note else {},
        )
        self._db.commit()
        return self._view(exercise)

    def tick_checklist(
        self, user: CurrentUser, exercise_id: str, item_key: str, done: bool
    ) -> ExerciseView:
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        updated = self._apply_tick(exercise, item_key, done, actor_id=user.id)
        if not updated:
            raise ExerciseNotFoundError(f"查無勾稽項 {item_key}", details={"item_key": item_key})
        self._audit(
            exercise_id,
            user.id,
            ACTION_CHECKLIST_TICKED,
            detail={"item_key": item_key, "done": done},
        )
        self._db.commit()
        return self._view(exercise)

    def attach_session(
        self, user: CurrentUser, exercise_id: str, req: AttachSessionRequest
    ) -> ExerciseView:
        """把**既有**的一局掛進演習。

        刻意不用 `clone_session` 生新局：那條路徑會掉七個想定衍生欄
        （msel/roe/mobilityOverrides/noStrikeZones/requestQuotas/…），
        預推局會沒有 MSEL、沒有 ROE、沒有禁射區地跑（已記 PROGRESS Backlog）。
        """
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        session = self._db.get(WargameSession, req.session_id)
        if session is None:
            raise ExerciseNotFoundError("查無此推演局", details={"session_id": req.session_id})
        if session.exercise_id and session.exercise_id != exercise_id:
            raise ExerciseSessionConflictError(
                "該局已掛在別的演習底下——一局只能屬於一個演習",
                details={"session_id": req.session_id, "exercise_id": session.exercise_id},
            )
        session.exercise_id = exercise_id
        session.session_role = req.session_role
        self._audit(
            exercise_id,
            user.id,
            ACTION_SESSION_ATTACHED,
            detail={
                "session_id": req.session_id,
                "session_role": req.session_role.value if req.session_role else None,
            },
        )
        self._db.commit()
        return self._view(exercise)

    def detach_session(self, user: CurrentUser, exercise_id: str, session_id: str) -> ExerciseView:
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        session = self._db.get(WargameSession, session_id)
        if session is None or session.exercise_id != exercise_id:
            raise ExerciseNotFoundError("該局不在此演習底下", details={"session_id": session_id})
        session.exercise_id = None
        session.session_role = None
        self._audit(
            exercise_id, user.id, ACTION_SESSION_DETACHED, detail={"session_id": session_id}
        )
        self._db.commit()
        return self._view(exercise)

    # ---- 撤收建檔與銷毀（WP-B1b）----

    def build_archive_bundle(self, user: CurrentUser, exercise_id: str) -> dict[str, Any]:
        """撤收建檔：整場演習的歸檔封包（帳本原樣 + AAR 統計 + 想定包 + 稽核軌跡）。

        **封包是 ground truth**（不套陣營投影），故限全知。匯出本身也留痕——
        「誰在什麼時候把整場演習的完整資料帶走了」是資安要問的第一個問題。
        """
        self._require_view(user)
        exercise = self._get(exercise_id)
        bundle = build_bundle(self._db, exercise)
        self._audit(
            exercise_id,
            user.id,
            ACTION_BUNDLE_EXPORTED,
            detail={
                "content_hash": bundle["content_hash"],
                "sessions": len(bundle["sessions"]),
            },
        )
        self._db.commit()
        return bundle

    def destroy_data(
        self, user: CurrentUser, exercise_id: str, confirm_name: str, redis_url: str
    ) -> dict[str, Any]:
        """銷毀模式（[JCATS-A p.16] 的資安要求）：硬刪本演習所有 session 的資料。

        三道閘門，每一道都擋掉一種真實的誤操作：

        1. **限 ADMIN**。`is_omniscient` 包含每一位白軍幕僚——用它等於把不可逆的銷毀
           開放給整個統裁組。這是 repo 裡第一個嚴格 ADMIN 閘門（既有的三份角色集都不是）。
        2. **必須已經 ARCHIVED**。還在跑的演習不會有人想銷毀資料；反過來說，
           要求先走完階段機，就保證了「該匯出的已經匯出」有機會發生。
        3. **`confirm_name` 必須與演習名稱逐字相符**。二次確認若只是「再按一次是」，
           那不是確認，是多按一次。

        **演習專案本身留下來**（連同稽核軌跡）——銷毀的是推演資料，
        而「這場演習存在過、在什麼時候被誰銷毀」正是稽核要保留的東西。
        """
        if user.role is not UserRole.ADMIN:
            raise AuthForbiddenError("銷毀推演資料限系統管理員（ADMIN）")
        exercise = self._get(exercise_id)
        if exercise.phase is not ExercisePhase.ARCHIVED:
            raise ExercisePhaseInvalidError(
                "只有已撤收（ARCHIVED）的演習可以銷毀資料",
                details={"phase": exercise.phase.value},
            )
        if confirm_name != exercise.name:
            raise ExerciseDestroyConfirmError(
                "確認名稱與演習名稱不符", details={"expected": exercise.name}
            )
        sessions = self._sessions_of(exercise_id)
        rows: dict[str, int] = {}
        redis_keys = 0
        for session in sessions:
            for table, n in purge_session_rows(self._db, session.id).items():
                rows[table] = rows.get(table, 0) + n
            with contextlib.suppress(Exception):
                # Redis 連不上不該讓 DB 那半邊回滾——**DB 才是真相**，
                # 而殘留的 Redis 鍵沒有 session 可依附，下一次 runner 掃描就不會碰它們。
                redis_keys += purge_session_redis(redis_url, session.id)
        summary = {
            "sessions_destroyed": len(sessions),
            "rows_deleted": rows,
            "redis_keys_deleted": redis_keys,
        }
        self._audit(exercise_id, user.id, ACTION_DESTROYED, detail=summary)
        self._db.commit()
        return summary

    # ---- 參數簽證（WP-B4）----

    def seal_params(self, user: CurrentUser, exercise_id: str) -> SealView:
        """簽證：把當下的全域參數快照起來並鎖住（[JCATS-A]「參數確認後簽證鎖定不得再改」）。

        **簽證是 REHEARSAL 期間的明示動作，不是進 EXECUTION 的副作用**。
        規格說「進入 EXECUTION 時執行 freeze」，但 `params_sealed` 同時是離開 REHEARSAL 的
        必要勾稽項——那樣寫會死鎖：要進 EXECUTION 得先勾，而勾是進去以後才發生的。
        改成明示動作也更貼近條文本身（「參數**確認後**簽證鎖定」——確認是人做的事）。

        重複簽證 → **重新快照**（參數又調了、要重新確認是正常流程），並記一筆稽核。
        """
        self._require_white_cell(user)
        exercise = self._get(exercise_id)
        payload = build_seal_payload(self._db)
        content_hash = compute_seal_hash(payload)
        seal = seal_for(self._db, exercise_id)
        if seal is None:
            seal = ParameterSeal(exercise_id=exercise_id, sealed_by=user.id, content_hash="")
            self._db.add(seal)
        seal.sealed_by = user.id
        seal.sealed_at = _now()
        seal.content_hash = content_hash
        seal.snapshot_blob = compress(payload)
        self._apply_tick(exercise, "params_sealed", True, actor_id=user.id)
        self._audit(exercise_id, user.id, ACTION_SEALED, detail={"content_hash": content_hash})
        self._db.commit()
        return self._seal_view(seal)

    def unseal_params(self, user: CurrentUser, exercise_id: str) -> None:
        """解除簽證。

        **這不是繞過閘門，是改變狀態**——一個會進稽核軌跡的明示動作。
        沒有它，一場被忘記的演習會讓全域武器庫永遠唯讀（`active_seal` 看的是 phase，
        而 phase 只能往前推、推不動就卡住）。留一條有痕跡的路，好過讓人去改 DB。
        """
        self._require_white_cell(user)
        self._get(exercise_id)
        seal = seal_for(self._db, exercise_id)
        if seal is None:
            raise ExerciseNotFoundError("此演習沒有簽證", details={"exercise_id": exercise_id})
        old = seal.content_hash
        self._db.delete(seal)
        exercise = self._get(exercise_id)
        self._apply_tick(exercise, "params_sealed", False, actor_id=user.id)
        self._audit(exercise_id, user.id, ACTION_UNSEALED, detail={"content_hash": old})
        self._db.commit()

    def get_seal(self, user: CurrentUser, exercise_id: str) -> SealView | None:
        self._require_view(user)
        self._get(exercise_id)
        seal = seal_for(self._db, exercise_id)
        return self._seal_view(seal) if seal is not None else None

    def _seal_view(self, seal: ParameterSeal) -> SealView:
        """簽證視圖。**當前雜湊一起回**——白軍要看得出「現在的參數還跟簽證時一樣嗎」，
        而不是等到開局被拒才發現。"""
        current = compute_seal_hash(build_seal_payload(self._db))
        return SealView(
            exercise_id=seal.exercise_id,
            sealed_at=_iso(seal.sealed_at) or "",
            sealed_by=seal.sealed_by,
            content_hash=seal.content_hash,
            current_hash=current,
            matches=current == seal.content_hash,
        )

    # ---- 供 WP-B4 的程式端掛點 ----

    def tick_checklist_by_system(self, exercise_id: str, item_key: str, actor_id: str) -> None:
        """程式自動勾稽（WP-B4 簽證完成即勾 `params_sealed`）。**不做權限檢查**——
        呼叫端已經是通過授權的動作，這裡再檢一次只會讓 B4 得先偽造一個 user。"""
        exercise = self._db.get(Exercise, exercise_id)
        if exercise is None:
            return
        if self._apply_tick(exercise, item_key, True, actor_id=actor_id):
            self._audit(
                exercise_id,
                actor_id,
                ACTION_CHECKLIST_TICKED,
                detail={"item_key": item_key, "done": True, "by_system": True},
            )

    # ---- 內部 ----

    def _apply_tick(self, exercise: Exercise, item_key: str, done: bool, *, actor_id: str) -> bool:
        """回傳是否真的找到並改了該項。

        整包重指派而非就地改：`checklist_json` 是 JSON 欄，SQLAlchemy 不會偵測到
        巢狀 list/dict 的就地變更，改了不會落盤（本 repo 的既有陷阱）。
        """
        items = list(exercise.checklist_json or [])
        found = False
        for i, item in enumerate(items):
            if item.get("key") != item_key:
                continue
            items[i] = {
                **item,
                "done": done,
                "done_at": _now().isoformat() if done else None,
                "done_by": actor_id if done else None,
            }
            found = True
            break
        if found:
            exercise.checklist_json = items
        return found

    def _audit(
        self,
        exercise_id: str,
        actor_id: str,
        action: str,
        *,
        from_phase: ExercisePhase | None = None,
        to_phase: ExercisePhase | None = None,
        detail: dict[str, Any] | None = None,
    ) -> None:
        self._db.add(
            ExerciseAuditLog(
                exercise_id=exercise_id,
                seq=self._next_seq(exercise_id),
                actor_id=actor_id,
                action=action,
                from_phase=from_phase,
                to_phase=to_phase,
                detail=detail or {},
            )
        )

    def _next_seq(self, exercise_id: str) -> int:
        """演習內的下一個稽核序號。

        `max(seq) + 1` 而非 count：稽核列**不會被刪**（FK cascade 只在整個演習被刪時觸發，
        那時序號也跟著消失），所以兩者現在等價——但用 max 的話，未來若真出現空洞
        也不會產生重號撞上 unique 約束。
        """
        from sqlalchemy import func as sa_func

        current = self._db.execute(
            select(sa_func.max(ExerciseAuditLog.seq)).where(
                ExerciseAuditLog.exercise_id == exercise_id
            )
        ).scalar()
        return int(current) + 1 if current is not None else 0

    def _get(self, exercise_id: str) -> Exercise:
        exercise = self._db.get(Exercise, exercise_id)
        if exercise is None:
            raise ExerciseNotFoundError("查無此演習", details={"exercise_id": exercise_id})
        return exercise

    def _sessions_of(self, exercise_id: str) -> list[WargameSession]:
        return list(
            self._db.execute(
                select(WargameSession)
                .where(WargameSession.exercise_id == exercise_id)
                .order_by(WargameSession.start_time, WargameSession.id)
            )
            .scalars()
            .all()
        )

    def _view(self, exercise: Exercise) -> ExerciseView:
        return ExerciseView(
            id=exercise.id,
            name=exercise.name,
            phase=exercise.phase,
            created_by=exercise.created_by,
            created_at=_iso(exercise.created_at) or "",
            phase_changed_at=_iso(exercise.phase_changed_at),
            schedule=dict(exercise.schedule_json or {}),
            checklist=[ExerciseChecklistItem(**item) for item in (exercise.checklist_json or [])],
            sessions=[
                ExerciseSessionRef(
                    id=s.id,
                    name=s.name,
                    status=(
                        "ARCHIVED"
                        if s.archived_at is not None
                        else "ENDED"
                        if s.end_time is not None
                        else "ACTIVE"
                    ),
                    session_role=s.session_role,
                    archived_at=_iso(s.archived_at),
                )
                for s in self._sessions_of(exercise.id)
            ],
        )

    @staticmethod
    def _require_view(user: CurrentUser) -> None:
        if not is_omniscient(user.role):
            raise ExerciseNotFoundError("查無此演習")  # 不區分「無權」與「不存在」以防列舉

    @staticmethod
    def _require_white_cell(user: CurrentUser) -> None:
        """推階段/勾稽/掛卸限白軍（統裁 + 白軍幕僚）。

        **ADMIN 刻意排除**：系統管理不是統裁（`faction_filter` 的既有裁示）。
        看得到、推不動——系統管理員不該替導演做決定。

        非全知者一律回 404 而不是 403：403 會回答「這個 id 存在」，那是列舉的入口。
        """
        if not is_omniscient(user.role):
            raise ExerciseNotFoundError("查無此演習")
        if not is_white_cell(user.role):
            raise AuthForbiddenError("僅白軍/統裁可操作演習專案")
