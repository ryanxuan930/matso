"""編裝（ORBAT）編輯 REST（#6）——White Cell 編輯單位參數 + 設定各軍自編權限。

PATCH /api/v1/sessions/{id}/units/{uid}         編輯單位（番號 / 兵科 / 人數 / 戰力 / attributes）
GET   /api/v1/sessions/{id}/orbat-permissions   取自編權限（限白軍）
PUT   /api/v1/sessions/{id}/orbat-permissions   設自編權限（限白軍）

權限：White Cell（全知）恆可編任一單位；一般指揮官僅在「其陣營 ∈ 自編清單」且單位為本軍時可編。

## 改了要真的生效

`designation` / `branch` 純顯示，寫 DB 就夠（`GET /units` 讀的就是 DB）。
但**人數與戰力不是**：它們在開局時被播種進熱狀態，此後裁決層只讀熱狀態。
只寫 DB 的話畫面上人數變了、實際打起來還是舊編制——那正是這個 repo 反覆出現的
那類缺陷。故本模組同時把變動推進 `live_unit` 命令通道（單一寫入者，見該模組說明）。

`faction` 與 `health_status` **不開放編輯**：前者改了會讓已存在的敵情、關係矩陣與
迷霧投影全部對不上；後者是由戰力比導出的顯示值，裁決層每次命中都會覆寫它，
開放編輯只會讓人以為自己「補血」了。要改戰力請改 `current_strength`。
"""

from __future__ import annotations

import contextlib
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.adjudication.effectiveness import effectiveness_pct
from app.adjudication.establishment import platform_count_for
from app.api.deps import get_current_user, get_db, get_settings
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.config import Settings
from app.errors import AuthForbiddenError, OrderValidationError, SessionNotFoundError
from app.factions import validate_faction_id
from app.models import TacticalUnit, UnitBranch, WargameSession
from app.models.enums import UnitLevel
from app.state.live_unit import push_unit_cmd
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["orbat"])


class UnitEdit(BaseModel):
    """可編欄位。全部 optional——只送要改的那幾個（PATCH 語義）。"""

    designation: str | None = Field(None, min_length=1, max_length=64)
    branch: UnitBranch | None = None  # 兵科（地圖符號的圖示）
    unit_level: UnitLevel | None = None  # 編制級別（影響聚合裁決門檻與建制數導出）
    personnel_current: int | None = Field(None, ge=0, le=100_000)  # 現員人數
    authorized_strength: float | None = Field(None, gt=0, le=1_000_000)  # 滿編戰力
    current_strength: float | None = Field(None, ge=0, le=1_000_000)  # 當前戰力
    attributes: dict[str, Any] | None = None
    # 保留：舊 client 仍可能送。**不再有作用**（見模組說明），送了會被明確拒絕而非靜默忽略。
    health_status: float | None = Field(None, ge=0, le=100)


class UnitEditView(BaseModel):
    id: str
    designation: str
    branch: str
    faction: str
    unit_level: str
    health: float
    strength: float
    authorized_strength: float
    personnel_current: int | None
    platform_count: int
    attributes: dict[str, Any]
    # 這次編輯有沒有東西要等 runner 重啟才生效（見 `_needs_restart`）。
    restart_required: bool = False


class OrbatPermissions(BaseModel):
    factions: list[str] = Field(default_factory=list)


def _session_or_404(db: Session, session_id: str) -> WargameSession:
    session = db.get(WargameSession, session_id)
    if session is None:
        raise SessionNotFoundError(f"session 不存在：{session_id}")
    return session


@lru_cache(maxsize=1)
def _redis(url: str) -> Any:
    return make_redis(url)


def _view(unit: TacticalUnit, *, restart_required: bool = False) -> UnitEditView:
    return UnitEditView(
        id=unit.id,
        designation=unit.designation,
        branch=unit.branch.value,
        faction=unit.faction,
        unit_level=unit.unit_level.value,
        health=unit.health_status,
        strength=unit.current_strength,
        authorized_strength=unit.authorized_strength,
        personnel_current=unit.personnel_current,
        platform_count=platform_count_for(
            unit.unit_level.value, unit.attributes, unit.personnel_current
        ),
        attributes=unit.attributes or {},
        restart_required=restart_required,
    )


@router.patch("/{session_id}/units/{unit_id}", response_model=UnitEditView)
def edit_unit(
    session_id: str,
    unit_id: str,
    edit: UnitEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UnitEditView:
    session = _session_or_404(db, session_id)
    unit = db.get(TacticalUnit, unit_id)
    if unit is None or unit.session_id != session_id:
        raise SessionNotFoundError("單位不存在於此 session")
    # 權限：白軍全開；一般角色需「本軍 + 該局開放自編」。
    if not is_omniscient(user.role):
        participant = require_participant(db, user, session_id)
        allowed = set(session.orbat_edit_factions or [])
        if participant.faction not in allowed or unit.faction != participant.faction:
            raise AuthForbiddenError("無編裝編輯權限（需白軍，或本軍且該局開放自編）")

    if edit.health_status is not None:
        # **明確拒絕勝過靜默忽略**：這個欄位每次命中都會被裁決層覆寫，
        # 接受它等於讓下令者以為自己補了血。
        raise OrderValidationError(
            "作戰效能（health）由戰力比導出、裁決層每次命中都會覆寫，不可直接編輯；"
            "要調整請改 current_strength",
            error_code="UNIT_EDIT_DERIVED_FIELD",
        )

    if edit.designation is not None:
        unit.designation = edit.designation
    if edit.branch is not None:
        unit.branch = edit.branch
    if edit.unit_level is not None:
        unit.unit_level = edit.unit_level
    if edit.personnel_current is not None:
        unit.personnel_current = edit.personnel_current
    if edit.authorized_strength is not None:
        unit.authorized_strength = edit.authorized_strength
    if edit.current_strength is not None:
        unit.current_strength = edit.current_strength
    if edit.attributes is not None:
        unit.attributes = {**(unit.attributes or {}), **edit.attributes}

    # 戰力比一動，作戰效能就得跟著重算——它是導出量，不是獨立欄位。
    if edit.current_strength is not None or edit.authorized_strength is not None:
        auth = unit.authorized_strength or 0.0
        unit.health_status = effectiveness_pct(unit.current_strength / auth) if auth > 0 else 0.0

    db.commit()

    _push_live(settings, session_id, unit, edit)
    return _view(unit, restart_required=_needs_restart(edit))


def _push_live(settings: Settings, session_id: str, unit: TacticalUnit, edit: UnitEdit) -> None:
    """把會影響裁決的欄位推進熱狀態命令通道。Redis 掛掉不擋編輯（DB 才是權威）。"""
    patch: dict[str, Any] = {}
    if edit.current_strength is not None:
        patch["strength"] = unit.current_strength
        patch["health"] = unit.health_status
    if edit.authorized_strength is not None:
        patch["authorized_strength"] = unit.authorized_strength
        patch["health"] = unit.health_status
    if edit.personnel_current is not None or edit.attributes is not None:
        patch["platform_count"] = platform_count_for(
            unit.unit_level.value, unit.attributes, unit.personnel_current
        )
    if not patch:
        return
    with contextlib.suppress(Exception):  # Redis 不可用不該讓編輯整個失敗（DB 才是權威）
        push_unit_cmd(_redis(settings.redis_url), session_id, unit.id, patch)


def _needs_restart(edit: UnitEdit) -> bool:
    """有沒有改到「runner 啟動時讀一次」的東西。

    `unit_level` 是其中之一：聚合裁決門檻在 runner 起跑時判定，而武器/機動解析器
    的快取也是那一刻建的。不告訴使用者的話，他會改完級別、看畫面確實變了，
    然後以為聚合裁決也跟著換了——實際上要等這一局的 runner 重啟。
    """
    return edit.unit_level is not None


@router.get("/{session_id}/orbat-permissions", response_model=OrbatPermissions)
def get_orbat_permissions(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrbatPermissions:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅白軍可檢視自編權限")
    session = _session_or_404(db, session_id)
    return OrbatPermissions(factions=list(session.orbat_edit_factions or []))


@router.put("/{session_id}/orbat-permissions", response_model=OrbatPermissions)
def set_orbat_permissions(
    session_id: str,
    perms: OrbatPermissions,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> OrbatPermissions:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅白軍可設定自編權限")
    session = _session_or_404(db, session_id)
    factions = [validate_faction_id(f) for f in perms.factions]
    session.orbat_edit_factions = factions
    db.commit()
    return OrbatPermissions(factions=factions)
