"""演習專案 REST（WP-B1，SPEC_V2 §6）。

`/api/v1/exercises*`。與 `api/lobby.py` 同紀律：**router 不做任何授權判斷**，
一律委派給 service——授權散在 router 就會出現「這個端點忘了檢查」那類洞。
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response, status
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.exercise.schemas import (
    AdvancePhaseRequest,
    AttachSessionRequest,
    ChecklistTickRequest,
    CreateExerciseRequest,
    DestroyExerciseDataRequest,
    DestroyResult,
    ExerciseAuditEntry,
    ExerciseView,
)
from app.exercise.service import ExerciseService

router = APIRouter(prefix="/api/v1/exercises", tags=["exercises"])


def get_exercise_service(db: Session = Depends(get_db)) -> ExerciseService:
    return ExerciseService(db)


@router.get("", response_model=list[ExerciseView])
def list_exercises(
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> list[ExerciseView]:
    """演習清單。**限白軍/統裁/管理**——演習層是導演工具。"""
    return svc.list_exercises(user)


@router.post("", status_code=201, response_model=ExerciseView)
def create_exercise(
    req: CreateExerciseRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    """建立演習（起始階段 PREP，附預設整備 checklist）。"""
    return svc.create_exercise(user, req)


@router.get("/{exercise_id}", response_model=ExerciseView)
def get_exercise(
    exercise_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    return svc.get_exercise(user, exercise_id)


@router.delete("/{exercise_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_exercise(
    exercise_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> Response:
    """刪演習專案本身。**不動任何 session**——掛在底下的局改回獨立局。"""
    svc.delete_exercise(user, exercise_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{exercise_id}/phase", response_model=ExerciseView)
def advance_exercise_phase(
    exercise_id: str,
    req: AdvancePhaseRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    """推進階段（只能沿序前進一階；未完成的必要整備項會擋下）。"""
    return svc.advance_phase(user, exercise_id, req)


@router.patch("/{exercise_id}/checklist/{item_key}", response_model=ExerciseView)
def tick_exercise_checklist(
    exercise_id: str,
    item_key: str,
    req: ChecklistTickRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    return svc.tick_checklist(user, exercise_id, item_key, req.done)


@router.post("/{exercise_id}/sessions", response_model=ExerciseView)
def attach_session_to_exercise(
    exercise_id: str,
    req: AttachSessionRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    """把既有的一局掛進本演習（不複製、不新建）。"""
    return svc.attach_session(user, exercise_id, req)


@router.delete("/{exercise_id}/sessions/{session_id}", response_model=ExerciseView)
def detach_session_from_exercise(
    exercise_id: str,
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> ExerciseView:
    """把一局從演習卸下（該局變回獨立局；**不刪任何資料**）。"""
    return svc.detach_session(user, exercise_id, session_id)


@router.get("/{exercise_id}/audit", response_model=list[ExerciseAuditEntry])
def get_exercise_audit(
    exercise_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> list[ExerciseAuditEntry]:
    return svc.get_audit(user, exercise_id)


@router.get("/{exercise_id}/bundle")
def get_exercise_bundle(
    exercise_id: str,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
) -> dict[str, Any]:
    """撤收建檔：整場演習的歸檔封包（單一 JSON 信封）。"""
    return svc.build_archive_bundle(user, exercise_id)


@router.post("/{exercise_id}/destroy", response_model=DestroyResult)
def destroy_exercise_data(
    exercise_id: str,
    req: DestroyExerciseDataRequest,
    user: CurrentUser = Depends(get_current_user),
    svc: ExerciseService = Depends(get_exercise_service),
    settings: Settings = Depends(get_settings),
) -> DestroyResult:
    """銷毀模式：硬刪本演習所有 session 的資料。限 ADMIN + 已 ARCHIVED + 名稱逐字確認。"""
    return DestroyResult(
        **svc.destroy_data(user, exercise_id, req.confirm_name, settings.redis_url)
    )
