"""演習 REST 載荷（WP-B1）——對映 core_api.yaml 的 Exercise* schemas。"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.models.enums import ExercisePhase, SessionRole


class CreateExerciseRequest(BaseModel):
    name: str = Field(min_length=1)
    schedule: dict[str, Any] = Field(default_factory=dict)


class AdvancePhaseRequest(BaseModel):
    """推進到下一階段。**只接受序列上的下一階**——跳階與倒退皆 EXERCISE_PHASE_INVALID。"""

    phase: ExercisePhase
    note: str | None = None


class ChecklistTickRequest(BaseModel):
    done: bool


class AttachSessionRequest(BaseModel):
    session_id: str = Field(min_length=1)
    session_role: SessionRole | None = None


class ExerciseChecklistItem(BaseModel):
    key: str
    label: str
    phase: ExercisePhase
    required: bool
    done: bool
    done_at: str | None = None
    done_by: str | None = None


class ExerciseSessionRef(BaseModel):
    """掛在演習底下的一局。`status` 由 session 導出——**與演習階段是兩條獨立的軸**。"""

    id: str
    name: str
    status: str
    session_role: SessionRole | None = None
    archived_at: str | None = None


class ExerciseView(BaseModel):
    id: str
    name: str
    phase: ExercisePhase
    created_by: str
    created_at: str
    phase_changed_at: str | None = None
    schedule: dict[str, Any] = Field(default_factory=dict)
    checklist: list[ExerciseChecklistItem] = Field(default_factory=list)
    sessions: list[ExerciseSessionRef] = Field(default_factory=list)


class ExerciseAuditEntry(BaseModel):
    id: str
    at: str
    actor_id: str
    action: str
    from_phase: ExercisePhase | None = None
    to_phase: ExercisePhase | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


class DestroyExerciseDataRequest(BaseModel):
    """銷毀確認。`confirm_name` 必須與演習名稱**逐字相符**。"""

    confirm_name: str = Field(min_length=1)


class DestroyResult(BaseModel):
    sessions_destroyed: int
    rows_deleted: dict[str, int] = Field(default_factory=dict)
    redis_keys_deleted: int = 0
