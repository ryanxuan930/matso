"""Units REST 端點（O4.5，SPEC §16.1）——faction-scoped 單位列表（下令 UX 需真單位）。

GET /api/v1/sessions/{id}/units —— 一般角色見己方單位；全知（統裁/白軍/管理）見全部。

WP-C5 起本端點是**位置凍結**的投影點之一（SPEC_FULL §6.2）：陣營視角下，通聯非 ONLINE 的
單位回報的是它**最後一次位置回報**（附 `stale_since_tick`），不是真實座標。全知的 god view
一律真實座標——「己方 COP 看不到」與「統裁看不到」是兩回事。
同時通聯狀態改以熱狀態為準：`TacticalUnit.comms_status` 自播種後從未被寫過，活模擬中
只有熱狀態的 `comms_state` 是真的。
"""

from __future__ import annotations

import contextlib
from collections.abc import Mapping
from functools import lru_cache
from typing import Any

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adjudication import WeaponProfile
from app.api.deps import get_current_user, get_db, get_settings
from app.api.session_scope import require_participant
from app.auth.schemas import CurrentUser
from app.cache import make_redis
from app.comms import parse_link_state, project_position
from app.config import Settings
from app.errors import AuthForbiddenError, SessionNotFoundError
from app.factions import validate_faction_id
from app.factions.session_store import load_session_relations
from app.factions.visibility import visible_factions
from app.models import EquipmentInstance, EquipmentTemplate, TacticalUnit
from app.state.comms_view import EMPTY_COMMS_VIEW, CommsView, load_comms_view
from app.state.live_position import push_pos_cmd
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["units"])


class UnitView(BaseModel):
    id: str
    designation: str
    unit_level: str
    branch: str  # 兵科（地圖符號的 2525C function ID 來源）
    faction: str
    lat: float | None
    lng: float | None
    health: float  # 作戰效能%（由戰力比導出）
    strength: float  # 當前戰力（權威）
    authorized_strength: float  # 滿編戰力
    platform_count: int  # 平台/建制數
    personnel_current: int | None = None  # 當前人員數（顯示用）
    comms: str
    is_fixed: bool = False  # 固定單位（指揮部等）：不可下 MOVE 令；COP 顯示鎖定標記
    # WP-C5 位置凍結：非 None ＝ lat/lng 是「最後一次位置回報」而非真實位置，本欄為其 tick。
    stale_since_tick: int | None = None
    # WP-C1 壓制與姿態。**只在自己陣營的單位上供應**——見 `_view` 的說明。
    suppression: float = 0.0
    posture: str = "MOVING"


class WeaponView(BaseModel):
    """單位可用武器（資料驅動 baseStats）——供 ENGAGE 前端選武器/彈種。"""

    id: str
    template_id: str
    name: str
    category: str
    max_range_m: float | None
    min_range_m: float
    ammo_types: list[str]
    ammo_remaining: int | None


def _view(
    u: TacticalUnit,
    comms: CommsView = EMPTY_COMMS_VIEW,
    *,
    scoped: bool = False,
    own: bool = True,
) -> UnitView:
    """單位視圖。`scoped=True`（陣營視角）才套用位置凍結；god view 一律真實座標。

    `own=False`（他方單位）→ **不供應壓制度與姿態**：看得到敵軍被壓制多少，
    等於一份免費的即時戰果評估——那正是 WP-C10.4 花整張卡在擋的東西。
    姿態同理（對方掘壕到什麼程度是要靠偵察才知道的）。

    盟軍算「己方」（聯絡官會回報）；god view 沒有他方，全供應。
    """
    hot = comms.units.get(u.id)
    lat, lng, stale = u.current_lat, u.current_lng, None
    projected = project_position(hot) if scoped and hot is not None else None
    if projected is not None:
        lat, lng, stale = projected.lat, projected.lng, projected.stale_since_tick
    return UnitView(
        id=u.id,
        designation=u.designation,
        unit_level=u.unit_level.value,
        branch=u.branch.value,
        faction=u.faction,
        lat=lat,
        lng=lng,
        health=u.health_status,
        strength=u.current_strength,
        authorized_strength=u.authorized_strength,
        platform_count=_platform_count(u),
        personnel_current=u.personnel_current,
        comms=_comms_value(u, hot),
        is_fixed=u.is_fixed,
        stale_since_tick=stale,
        suppression=_hot_float(hot, "suppression") if own else 0.0,
        posture=(_hot_str(hot, "posture") or "MOVING") if own else "MOVING",
    )


def _hot_float(hot: Mapping[str, Any] | None, key: str) -> float:
    raw = (hot or {}).get(key)
    return round(float(raw), 3) if isinstance(raw, (int, float)) else 0.0


def _hot_str(hot: Mapping[str, Any] | None, key: str) -> str:
    raw = (hot or {}).get(key)
    return str(raw) if isinstance(raw, str) else ""


def _comms_value(u: TacticalUnit, hot: Mapping[str, Any] | None) -> str:
    """通聯狀態以熱狀態為準；該局沒在跑（無熱狀態）才退回 DB 播種值。"""
    if hot is not None and hot.get("comms_state"):
        return parse_link_state(hot["comms_state"]).value
    return u.comms_status.value


def _platform_count(u: TacticalUnit) -> int:
    pc = u.attributes.get("platform_count") if isinstance(u.attributes, dict) else None
    if isinstance(pc, (int, float)) and pc >= 1:
        return int(pc)
    if isinstance(u.personnel_current, int) and u.personnel_current >= 1:
        return u.personnel_current
    return 1


def _visible_factions(db: Session, session_id: str, observer: str) -> list[str]:
    """觀測者直接看得到的陣營＝**自己 + 盟軍**（#91 共享視圖）。

    偵測 sweep 早就假定「己方與 ALLIED 不成 contact（盟軍經共享視圖，非偵測）」
    （`intel/sweep.py`），但那個共享視圖從來沒實作——units 一直是嚴格 `== faction`。
    #98 把關係矩陣接上後，盟軍變成既不在 units、也不在 contacts，等於互相隱形。此函式補上該視圖。

    規則本身在 `factions/visibility.py`（STATE_DIFF 的每陣營投影共用同一份，WP-C5）；
    這裡只負責把 DB 的資料餵給它。
    """
    observer = validate_faction_id(observer)
    rel = load_session_relations(db, session_id)
    factions = db.scalars(
        select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id).distinct()
    ).all()
    return visible_factions(observer, factions, rel)


@router.get("/{session_id}/units", response_model=list[UnitView])
def list_units(
    session_id: str,
    as_faction: str | None = Query(None, description="White Cell 視角切換（O7.4）"),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> list[UnitView]:
    # 全知（統裁/白軍/管理）由**使用者全域角色**判定，非 session 內參與者角色——EXERCISE_DIRECTOR
    # 即使以某軍 COMMANDER 身分參戰，仍具全知視角（與 WS resolve_ws_identity 一致，SPEC §12）。
    omniscient = is_omniscient(user.role)
    # 非全知者才需為此 session 參與者（faction-scope）；全知者（含非參與者的純白軍）放行。
    participant = None if omniscient else require_participant(db, user, session_id)
    stmt = select(TacticalUnit).where(TacticalUnit.session_id == session_id)

    # 友軍陣營集合（自己＋盟軍）。None ＝ god view，無「他方」可言。
    friendly: set[str] | None = None
    if as_faction is not None:
        # 視角切換（White Cell 控制台，O7.4）：僅全知可指定；非全知者禁止（防越權窺視）。
        if not omniscient:
            raise AuthForbiddenError("僅 White Cell 可切換視角")
        friendly = set(_visible_factions(db, session_id, as_faction))
        stmt = stmt.where(TacticalUnit.faction.in_(friendly))
    elif not omniscient:
        assert participant is not None  # 非全知 → 必為參與者（上方已 require）
        friendly = set(_visible_factions(db, session_id, participant.faction))
        if not settings.stub_gateway:
            # 一般角色：faction 過濾下推 SQL（C12）；STUB_GATEWAY E2E affordance 放行全單位
            # （但 `friendly` 仍是真的友軍集合——壓制度不因該 affordance 外洩）。
            stmt = stmt.where(TacticalUnit.faction.in_(friendly))
    # else：全知且未指定視角 → 全部（god view）

    units = db.execute(stmt).scalars().all()
    # WP-C5：陣營視角才凍結位置（god view ＝ 全知未指定視角 → scoped=False，看真實座標）。
    # 白軍指定 as_faction 時**要**凍結：那正是「這一軍看得到什麼」的問題（與 O7.4 視角語義一致）。
    scoped = as_faction is not None or not omniscient
    comms = load_comms_view(make_redis(settings.redis_url), session_id, [u.id for u in units])
    # `own`：壓制度只給友軍（含盟軍——那是聯絡官會回報的事）。god view 沒有他方，全給。
    return [
        _view(u, comms, scoped=scoped, own=(friendly is None or u.faction in friendly))
        for u in units
    ]


@router.get("/{session_id}/units/{unit_id}/weapons", response_model=list[WeaponView])
def list_unit_weapons(
    session_id: str,
    unit_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[WeaponView]:
    """單位可用武器（ENGAGE 選武器/彈種）。fog of war：全知見任一；否則須為參與者且為己方單位。

    絕不洩漏敵方 loadout——他方（或不存在）單位一律 AuthForbiddenError（與 list_units 一致）。
    """
    omniscient = is_omniscient(user.role)
    participant = None if omniscient else require_participant(db, user, session_id)
    unit = db.get(TacticalUnit, unit_id)
    if unit is None or unit.session_id != session_id:
        raise AuthForbiddenError("查無此單位")  # 不區分「不存在」與「他方」以防列舉
    if not omniscient:
        assert participant is not None  # 非全知 → 上方已 require_participant
        if unit.faction != participant.faction:
            raise AuthForbiddenError("無權查看他方單位裝備")

    instances = (
        db.execute(select(EquipmentInstance).where(EquipmentInstance.owner_id == unit.id))
        .scalars()
        .all()
    )
    out: list[WeaponView] = []
    for inst in instances:
        tmpl = db.get(EquipmentTemplate, inst.template_id)
        if tmpl is None:
            continue
        try:
            profile = WeaponProfile.from_base_stats(tmpl.base_stats)
        except ValueError:
            continue  # 非 KINETIC 武器 / baseStats 壞 → 略過（不列入可選武器）
        raw_ammo = inst.current_state.get("ammo") if isinstance(inst.current_state, dict) else None
        ammo_remaining = int(raw_ammo) if isinstance(raw_ammo, (int, float)) else None
        out.append(
            WeaponView(
                id=inst.id,
                template_id=tmpl.id,
                name=tmpl.name,
                category=tmpl.category,
                max_range_m=profile.max_range_m,
                min_range_m=profile.min_range_m,
                ammo_types=list(profile.ammo_types),
                ammo_remaining=ammo_remaining,
            )
        )
    return out


@lru_cache(maxsize=1)
def _reposition_redis(url: str) -> Any:
    return make_redis(url)


class RepositionRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


@router.post("/{session_id}/units/{unit_id}/reposition", response_model=UnitView)
def reposition_unit(
    session_id: str,
    unit_id: str,
    req: RepositionRequest,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> UnitView:
    """White Cell「地圖狀態編輯」：把單位直接放到新座標（拖放）。

    座標寫入 DB（權威，供顯示/reconnect/seed）+ 推入活模擬座標命令通道（sim 迴圈 drain 套 hot；
    暫停中編輯 → 開始兵推後第一 tick 生效）。限全知（統裁/白軍/管理）——編輯任一陣營位置是布局動作。
    """
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅統裁/白軍/管理可編輯單位位置（地圖狀態編輯）")
    unit = db.get(TacticalUnit, unit_id)
    if unit is None or unit.session_id != session_id:
        raise SessionNotFoundError("單位不存在於此 session")
    unit.current_lat = req.lat
    unit.current_lng = req.lng
    db.commit()
    # 命令通道失敗（無活 sim / redis 不可達）不讓 DB 編輯回滾；下次 seed 仍帶 DB 值。
    with contextlib.suppress(Exception):
        push_pos_cmd(_reposition_redis(settings.redis_url), session_id, unit_id, req.lat, req.lng)
    db.refresh(unit)
    return _view(unit)
