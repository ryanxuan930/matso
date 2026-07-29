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

import datetime as _dt
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.errors import (
    AuthForbiddenError,
    ExerciseChecklistIncompleteError,
    ExerciseNotFoundError,
    ExercisePhaseInvalidError,
    ExerciseSessionConflictError,
)
from app.exercise.phases import default_checklist, is_valid_transition, missing_required
from app.exercise.schemas import (
    AdvancePhaseRequest,
    AttachSessionRequest,
    CreateExerciseRequest,
    ExerciseAuditEntry,
    ExerciseChecklistItem,
    ExerciseSessionRef,
    ExerciseView,
)
from app.models import Exercise, ExerciseAuditLog, ExercisePhase, WargameSession
from app.stream.faction_filter import is_omniscient, is_white_cell

# 稽核 action 常數——字串散在各處會拼錯，而稽核軌跡拼錯了沒有人會發現。
ACTION_CREATED = "EXERCISE_CREATED"
ACTION_PHASE_ADVANCED = "PHASE_ADVANCED"
ACTION_CHECKLIST_TICKED = "CHECKLIST_TICKED"
ACTION_SESSION_ATTACHED = "SESSION_ATTACHED"
ACTION_SESSION_DETACHED = "SESSION_DETACHED"


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
