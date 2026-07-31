"""AAR REST 端點（O8.1–O8.4，SPEC_FULL §14）——重播/統計/敘事/匯出。

存取：參與者、ANALYST（僅 AAR）、全知（統裁/管理）。其餘 → 403。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.aar import read_events
from app.aar.export import export_csv, export_json
from app.aar.fog import project_events
from app.aar.missions import build_timelines
from app.aar.narrative import generate_narrative, verify_citations
from app.aar.replay import replay_summary, state_frames
from app.aar.stats import compute_metrics
from app.api.deps import get_current_user, get_db
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError
from app.models import SessionParticipant, TacticalUnit
from app.models.enums import UserRole
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/sessions", tags=["aar"])


class AarTimelineFrameView(BaseModel):
    """時間軸重播的一格：有事件的 tick 與該 tick 的事件型別。"""

    tick: int
    event_types: list[str] = Field(default_factory=list)


class AarBookmarkView(BaseModel):
    """時間軸書籤——值得跳過去看的關鍵時刻（交戰、收場等）。"""

    seq: int
    tick: int
    label: str


class AarReplayView(BaseModel):
    frames: list[AarTimelineFrameView] = Field(default_factory=list)
    bookmarks: list[AarBookmarkView] = Field(default_factory=list)
    total_events: int = 0
    max_tick: int = 0


class AarParagraphView(BaseModel):
    """敘事段落。`cited_seqs` 是它引用的帳本 seq——**查核就是查這些 seq 存不存在**。"""

    text: str
    cited_seqs: list[int] = Field(default_factory=list)


class AarCitationsView(BaseModel):
    """引用查核結果。`valid=False` 代表敘事引用了帳本裡沒有的 seq（捏造）。"""

    valid: bool = True
    invalid_seqs: list[int] = Field(default_factory=list)


class AarReportView(BaseModel):
    summary: str = ""
    paragraphs: list[AarParagraphView] = Field(default_factory=list)
    lessons: list[str] = Field(default_factory=list)
    citations: AarCitationsView = Field(default_factory=AarCitationsView)


class AarReplayChangeView(BaseModel):
    """單一單位在該 tick 的變動。**只列真的變了的欄位**，未列＝沿用前一狀態。

    ⚠ 端點必須以 `response_model_exclude_none=True` 送出。前端的累加邏輯判的是
    `c.lat !== undefined`——一旦改送 `null`，那個判斷會變成 true 而把座標設成 null，
    地圖重播就**靜默壞掉**（單位不是消失，是被畫到 null 座標）。
    """

    unit_id: str
    lat: float | None = None
    lng: float | None = None
    health: float | None = None  # 效能%（0–100）
    strength: float | None = None  # 戰力點——與 health **量綱不同不可互換**


class AarReplayFrameView(BaseModel):
    tick: int
    event_types: list[str] = Field(default_factory=list)
    changes: list[AarReplayChangeView] = Field(default_factory=list)


class AarReplayUnitView(BaseModel):
    """重播底本的單位（靜態屬性 + tick 0 基準狀態）。"""

    id: str
    designation: str | None = None
    faction: str
    unit_level: str | None = None
    is_fixed: bool = False
    authorized_strength: float | None = None
    base_lat: float | None = None
    base_lng: float | None = None
    base_health: float = 100.0


class AarReplayStatesView(BaseModel):
    """地圖重播的完整資料。

    這個端點過去回**裸 `dict`**——契約裡的 `AarReplayStates` 從來沒有被對照過後端，
    它可以整段說謊而沒有任何閘門會發現。宣告出來之後 FastAPI 每次回應都會驗。
    """

    units: list[AarReplayUnitView] = Field(default_factory=list)
    frames: list[AarReplayFrameView] = Field(default_factory=list)
    max_tick: int = 0


class MissionLegView(BaseModel):
    """任務時間軸上的一段。`to_tick`/`duration_ticks` 為 None ＝局結束時仍在這個階段。"""

    phase: str
    from_tick: int
    to_tick: int | None = None
    duration_ticks: int | None = None
    note: str = ""


class MissionTimelineView(BaseModel):
    """一道任務怎麼走完的（WP-A2）。

    這個端點過去回的是**裸 `list[dict]`**——FastAPI 一個欄位都不驗，契約裡也沒有它，
    於是前端型別只能人手抄；後端改個欄位名，畫面就靜默變空白而所有閘門都是綠的。
    宣告出來之後 `test_contract_conformance` 才管得到它。
    """

    order_id: str
    mission_type: str
    unit_id: str | None = None
    failed: bool = False
    errors: int = 0
    legs: list[MissionLegView] = Field(default_factory=list)


def _aar_visible_factions(db: Session, session_id: str, observer: str) -> list[str]:
    """觀看者在 AAR 裡看得到的陣營＝**自己 + 盟軍**（與 `/units` 的共享視圖同一條規則）。

    盟軍算得過是刻意的：#91 的共享視圖本來就讓盟軍互相看得到編成。
    """
    from app.factions import WHITE_CELL
    from app.factions.session_store import load_session_relations

    factions = db.scalars(
        select(TacticalUnit.faction).where(TacticalUnit.session_id == session_id).distinct()
    ).all()
    if observer == WHITE_CELL:
        return list(factions)
    relations = load_session_relations(db, session_id)
    return [f for f in factions if f == observer or relations.is_allied(observer, f)]


def require_aar_access(db: Session, user: CurrentUser, session_id: str) -> str | None:
    """AAR 存取：全知 / ANALYST / 本 session 參與者。其餘 → 403。

    回**觀看者的陣營**（全知/ANALYST 回 None ＝不受迷霧限制）。呼叫端據此投影事件——
    參與者在演習**進行中**就能 poll AAR，不投影的話等於一個沒有上鎖的敵情窗口。
    """
    if is_omniscient(user.role) or user.role is UserRole.ANALYST:
        return None
    participant = db.execute(
        select(SessionParticipant).where(
            SessionParticipant.user_id == user.id,
            SessionParticipant.session_id == session_id,
        )
    ).scalar_one_or_none()
    if participant is None:
        raise AuthForbiddenError("無 AAR 存取權（非參與者/ANALYST/統裁）")
    return str(participant.faction)


def _visible_events(db: Session, session_id: str, viewer_faction: str | None):  # type: ignore[no-untyped-def]
    """該觀看者看得到的事件流。全知/ANALYST → 原樣；其餘走與 WS feed 同一條受眾規則。"""
    events = read_events(db, session_id)
    if viewer_faction is None:
        return events
    return project_events(
        events,
        faction=viewer_faction,
        omniscient=False,
        faction_for=_unit_faction(db, session_id),
    )


def _unit_faction(db: Session, session_id: str) -> dict[str, str]:
    rows = (
        db.execute(
            select(TacticalUnit.id, TacticalUnit.faction).where(
                TacticalUnit.session_id == session_id
            )
        )
        .tuples()
        .all()
    )
    return dict(rows)


@router.get("/{session_id}/aar/replay", response_model=AarReplayView)
def get_replay(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    viewer = require_aar_access(db, user, session_id)
    s = replay_summary(_visible_events(db, session_id, viewer))
    return {
        "frames": [{"tick": f.tick, "event_types": f.event_types} for f in s.frames],
        "bookmarks": [{"seq": b.seq, "tick": b.tick, "label": b.label} for b in s.bookmarks],
        "total_events": s.total_events,
        "max_tick": s.max_tick,
    }


@router.get(
    "/{session_id}/aar/replay/states",
    response_model=AarReplayStatesView,
    # 見 `AarReplayChangeView` 的警語：改送 null 會讓地圖重播靜默壞掉。
    response_model_exclude_none=True,
)
def get_replay_states(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    """地圖重播的狀態流（WP-D6.1）：靜態底本 + 逐 tick 差異，整場一次給。

    **tick 0 基準位置是近似值，這是帳本的限制不是實作偷懶**：帳本裡沒有「部署」事件，
    白軍在「地圖狀態編輯」拖動單位也不落帳（`reposition_unit` 只寫 DB + 命令通道）。
    故基準取法為：
      * 該單位在帳本中**最早一筆有座標的事件** → 用它（離 tick 0 最近的已記錄真相；
        誤差最多一個移動步長，且僅影響它第一次移動之前的畫面）；
      * 完全沒有座標事件（從沒動過）→ 用 DB 現值（沒動過＝現值即初始，精確）。
    """
    viewer = require_aar_access(db, user, session_id)
    events = _visible_events(db, session_id, viewer)

    rows = (
        db.execute(
            select(
                TacticalUnit.id,
                TacticalUnit.designation,
                TacticalUnit.faction,
                TacticalUnit.unit_level,
                TacticalUnit.is_fixed,
                TacticalUnit.authorized_strength,
                TacticalUnit.current_lat,
                TacticalUnit.current_lng,
            ).where(TacticalUnit.session_id == session_id)
        )
        .tuples()
        .all()
    )
    # 紅線 3：**名冊也要投影**。`_visible_events` 早就把事件霧化了，但這份 rows 沒有
    # ——於是任一參與者 poll 這支 API 就拿到**全陣營的番號、編制與 tick 0 即時座標**。
    # docstring 自己都寫了「參與者在演習進行中就能 poll AAR，不投影的話等於一個
    # 沒有上鎖的敵情窗口」，事件做到了，名冊漏了。
    # `viewer is None` ＝全知/ANALYST → 不過濾（他們本來就有權看全部）。
    if viewer is not None:
        allowed = set(_aar_visible_factions(db, session_id, viewer))
        rows = [r for r in rows if r[2] in allowed]
    authorized = {r[0]: float(r[5]) for r in rows if r[5] is not None}
    frames = state_frames(events, authorized)

    # 每個單位最早一筆有座標的事件（見 docstring 的基準取法）。
    first_pos: dict[str, tuple[float, float]] = {}
    for e in events:
        src = e.detail if ("lat" in e.detail and "lng" in e.detail) else e.ai_decision
        if e.initiator_id and e.initiator_id not in first_pos and "lat" in src and "lng" in src:
            first_pos[e.initiator_id] = (float(src["lat"]), float(src["lng"]))

    units = []
    for uid, designation, faction, unit_level, is_fixed, auth, cur_lat, cur_lng in rows:
        base = first_pos.get(uid)
        units.append(
            {
                "id": uid,
                "designation": designation,
                "faction": faction,
                "unit_level": unit_level.value,
                "is_fixed": bool(is_fixed),
                "authorized_strength": float(auth) if auth is not None else None,
                "base_lat": base[0] if base else (float(cur_lat) if cur_lat is not None else None),
                "base_lng": base[1] if base else (float(cur_lng) if cur_lng is not None else None),
                "base_health": 100.0,
            }
        )
    return {
        "units": units,
        "frames": [
            {
                "tick": f.tick,
                "event_types": f.event_types,
                "changes": [
                    {
                        k: v
                        for k, v in (
                            ("unit_id", c.unit_id),
                            ("lat", c.lat),
                            ("lng", c.lng),
                            ("health", c.health),
                            ("strength", c.strength),
                        )
                        if v is not None
                    }
                    for c in f.changes
                ],
            }
            for f in frames
        ],
        "max_tick": frames[-1].tick if frames else 0,
    }


@router.get("/{session_id}/aar/stats")
def get_stats(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    viewer = require_aar_access(db, user, session_id)
    m = compute_metrics(_visible_events(db, session_id, viewer), _unit_faction(db, session_id))
    return {
        "total_events": m.total_events,
        "engagements": m.engagements,
        # attempts / engagements_fired 分開回：畫面要講得出「下了 40 次令、只有 12 次射得出去」，
        # 只給一個 hit_rate 講不出來（WP-D6.2）。
        "attempts": m.attempts,
        "engagements_fired": m.engagements_fired,
        "hits": m.hits,
        "hit_rate": m.hit_rate,
        "total_damage": m.total_damage,
        "guardrail_blocks": m.guardrail_blocks,
        "damage_by_faction": m.damage_by_faction,
        "event_counts": m.event_counts,
        "stats_version": m.stats_version,
    }


@router.get("/{session_id}/aar/missions", response_model=list[MissionTimelineView])
def get_mission_timelines(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[dict]:  # type: ignore[type-arg]
    """任務時間軸（WP-A2）：每道任務走過哪些階段、各花了多久。

    走 `_visible_events` ——**與其他 AAR 端點同一條迷霧路徑**。
    在這裡另做投影會是第二套規則，而兩套規則必然漂移。
    """
    viewer = require_aar_access(db, user, session_id)
    timelines = build_timelines(_visible_events(db, session_id, viewer))
    return [t.to_dict() for t in timelines]


@router.get("/{session_id}/aar/report", response_model=AarReportView)
def get_report(
    session_id: str,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> dict:  # type: ignore[type-arg]
    viewer = require_aar_access(db, user, session_id)
    events = _visible_events(db, session_id, viewer)
    narrative = generate_narrative(events)
    invalid = verify_citations(narrative, events)
    return {
        "summary": narrative.summary,
        "paragraphs": [{"text": p.text, "cited_seqs": p.cited_seqs} for p in narrative.paragraphs],
        "lessons": narrative.lessons,
        "citations": {"valid": not invalid, "invalid_seqs": invalid},
    }


@router.get("/{session_id}/aar/export")
def get_export(
    session_id: str,
    fmt: str = Query("json", pattern="^(json|csv)$"),
    anonymize: bool = Query(False),
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Response:
    viewer = require_aar_access(db, user, session_id)
    events = _visible_events(db, session_id, viewer)
    if fmt == "csv":
        return Response(export_csv(events, anonymize=anonymize), media_type="text/csv")
    return Response(export_json(events, anonymize=anonymize), media_type="application/json")
