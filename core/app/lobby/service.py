"""Lobby 服務（O4.1，SPEC §13.1）——session 列表（角色/參與過濾）+ 建局。

**faction-scope 後端強制（SPEC §12）**：一般角色只看得到自己參與的 session；統裁/管理角色
（EXERCISE_DIRECTOR / WHITE_CELL_STAFF / ADMIN）看得到全部。前端過濾不可信。

範圍（O4.1）：list + create。加入（join）與完整 session 生命週期（scenario 載入、kernel 生成）
屬後續卡（O7/O8）；本卡的 create 只建 WargameSession 列並讓建立者成為 EXERCISE_DIRECTOR 參與者。
"""

from __future__ import annotations

import hashlib
import time

from sqlalchemy import select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from app.auth.schemas import CurrentUser
from app.factions import WHITE_CELL
from app.lobby.purge import purge_session_rows
from app.lobby.schemas import (
    CloneSessionRequest,
    CreateSessionRequest,
    EditSessionRequest,
    SessionSummary,
)
from app.models import SessionParticipant, UserRole, WargameSession

# 看得到全部 session 的統裁/管理角色（其餘只看自己參與的）
_OMNISCIENT_ROLES = frozenset(
    {UserRole.EXERCISE_DIRECTOR, UserRole.WHITE_CELL_STAFF, UserRole.ADMIN}
)


# 刪局重試（見 `delete_session`）。runner 停下來要一輪掃描（3 秒），故總等待需超過它。
_DELETE_ATTEMPTS = 5
_DELETE_BACKOFF_S = 1.0


class LobbyService:
    def __init__(self, db: Session) -> None:
        self._db = db

    def list_sessions(self, user: CurrentUser) -> list[SessionSummary]:
        """依角色過濾的 session 列表。統裁/管理見全部，其餘僅見自己參與的。"""
        my_factions = self._participant_factions(user.id)
        my_scopes = self._participant_scopes(user.id)
        my_seats = self._participant_seats(user.id)
        if user.role in _OMNISCIENT_ROLES:
            sessions = self._db.execute(select(WargameSession)).scalars().all()
        else:
            session_ids = list(my_factions.keys())
            if not session_ids:
                return []
            sessions = (
                self._db.execute(select(WargameSession).where(WargameSession.id.in_(session_ids)))
                .scalars()
                .all()
            )
        omni = user.role in _OMNISCIENT_ROLES
        return [
            self._summary(
                s,
                my_factions.get(s.id),
                orbat_edit=omni or (my_factions.get(s.id) in set(s.orbat_edit_factions or [])),
                my_unit_scope=my_scopes.get(s.id, []),
                my_seat_role=my_seats.get(s.id),
            )
            for s in sessions
        ]

    def create_session(self, user: CurrentUser, req: CreateSessionRequest) -> SessionSummary:
        """建立 session 列，建立者成為 EXERCISE_DIRECTOR 參與者（WHITE_CELL faction）。

        帶 `scenario_id` → 由已存想定開局（建 session + orbat 單位 + relations，#7）；否則建空局。
        """
        if req.scenario_id:
            return self._create_from_scenario(user, req)
        session = WargameSession(
            name=req.name,
            scenario_id=req.scenario_id,
            master_seed=0,  # 佔位；flush 取得 session.id 後以其導出（見下）
            mode=req.mode,
            current_weather={},
        )
        self._db.add(session)
        self._db.flush()  # 取得 session.id
        # master_seed 摻入 session.id（uuid）避免「同名同人」建局的 RNG 流碰撞（CODE_REVIEW C15）。
        session.master_seed = _derive_seed(req.name, user.id, session.id)
        participant = SessionParticipant(
            user_id=user.id,
            session_id=session.id,
            faction=WHITE_CELL,
            role=UserRole.EXERCISE_DIRECTOR,
            unit_scope=[],
        )
        self._db.add(participant)
        self._db.commit()
        return self._summary(session, participant.faction, orbat_edit=True)  # 建立者為統裁（全知）

    def _create_from_scenario(self, user: CurrentUser, req: CreateSessionRequest) -> SessionSummary:
        """由已存想定開局（#7）：載回 bundle → 建 session + 單位 → 建立者為統裁參與者。"""
        import json

        from app.errors import ScenarioInvalidError, ScenarioNotFoundError
        from app.models import Scenario
        from app.scenario import ScenarioError, create_session_from_scenario, load_scenario_bundle

        row = self._db.get(Scenario, req.scenario_id)
        if row is None:
            raise ScenarioNotFoundError(f"想定不存在：{req.scenario_id}")
        try:
            loaded = load_scenario_bundle(json.loads(bytes(row.package_blob)))
        except ScenarioError as exc:
            raise ScenarioInvalidError(str(exc)) from exc
        seed = _derive_seed(loaded.name, user.id, str(req.scenario_id))
        sid = create_session_from_scenario(
            self._db,
            loaded,
            master_seed=seed,
            scenario_id=req.scenario_id,
            seed_default_equipment=True,  # 配發預設武器，供資料驅動的 ENGAGE 武器/彈種選擇
        )
        self._db.add(
            SessionParticipant(
                user_id=user.id,
                session_id=sid,
                faction=WHITE_CELL,
                role=UserRole.EXERCISE_DIRECTOR,
                unit_scope=[],
            )
        )
        self._db.commit()
        session = self._db.get(WargameSession, sid)
        assert session is not None
        return self._summary(session, WHITE_CELL, orbat_edit=True)  # 建立者為統裁（全知）

    def clone_session(
        self, user: CurrentUser, session_id: str, req: CloneSessionRequest
    ) -> SessionSummary:
        """複製一局為新推演（#79）：verbatim 複製當下 DB 狀態，另給新 master_seed。

        複製範圍：session 參數（名稱/模式/想定連結/天氣/世界時/自編陣營）、單位部署（座標/戰力/
        人員/健康/通信/固定旗標/階層）、裝備（templateId+數量+current_state）、地圖標註、參與者名冊
        （unit_scope 依 old→new 單位映射重寫；跳過 AI `ai-*` 帳號——runner 會重建）。

        無「初始快照」——DB 即活權威（sim 執行期寫回座標/戰力/彈藥）；**開打前複製＝純淨初始局**。
        新 master_seed（掺入新 session.id）→ 新一輪獨立 RNG 流。限統裁/管理（`_require_director`）。
        """
        from app.models import EquipmentInstance, MapFeature, TacticalUnit, User

        src = self._require_director(user, session_id)
        new_name = (req.name or "").strip() or f"{src.name}（副本）"
        new = WargameSession(
            name=new_name,
            scenario_id=src.scenario_id,
            master_seed=0,  # 佔位；flush 取得 id 後導出
            mode=src.mode,
            current_weather=dict(src.current_weather or {}),
            world_start_time=src.world_start_time,
            orbat_edit_factions=(
                list(src.orbat_edit_factions) if isinstance(src.orbat_edit_factions, list) else None
            ),
            # #98 複製關係矩陣——否則副本會退回全 HOSTILE，盟友關係憑空消失。
            faction_relations=(
                list(src.faction_relations) if isinstance(src.faction_relations, list) else None
            ),
            # ⚠ **以下七個是想定衍生欄，複製時全部漏掉過**（WP-B1 掃描發現）。
            # 漏掉的後果不是「少一點設定」，是**副本會沒有 MSEL、沒有 ROE、沒有禁射區地跑**
            # ——看起來一切正常，直到你發現腳本事件永遠不觸發、被禁的武器可以隨便用。
            # 這也是 B1 當初不敢建在 clone 上（改掛既有局）的原因。
            #
            # 新增想定層設定時**務必同時改這裡**（與 `scenario/dump.py` 的白名單同一個陷阱）。
            msel=_copy_json(src.msel),
            roe=_copy_json(src.roe),
            mobility_overrides=_copy_json(src.mobility_overrides),
            no_strike_zones=_copy_json(src.no_strike_zones),
            request_quotas=_copy_json(src.request_quotas),
            indirect_fire_requires_approval=src.indirect_fire_requires_approval,
            survivability_move=_copy_json(src.survivability_move),
            # WP-C9/C4a：本 session 新增的兩個，一併複製（否則同樣會靜靜消失）。
            allow_fratricide=src.allow_fratricide,
            day_night=_copy_json(src.day_night),
            # ⚠ **上面那句「務必同時改這裡」失敗了五次**——底下這五欄全是後來新增的想定衍生欄，
            # 每一次都漏掉。註解攔不住這種漏，所以另有一條 AST 守門測試釘住
            # （`test_clone_covers_every_session_column`）：模型加了欄位而這裡沒接，測試會紅。
            aggregate_adjudication_level=src.aggregate_adjudication_level,
            victory_conditions=_copy_json(src.victory_conditions),
            tick_rate_ms=src.tick_rate_ms,
            faction_colors=_copy_json(src.faction_colors),
            faction_display_names=_copy_json(src.faction_display_names),
        )
        self._db.add(new)
        self._db.flush()  # 取得 new.id
        new.master_seed = _derive_seed(new_name, user.id, new.id)

        # 單位：兩階段（先全建 → 再連 parent，避免順序相依）+ old→new 映射（供 parent/scope 重寫）。
        src_units = list(
            self._db.execute(
                select(TacticalUnit).where(TacticalUnit.session_id == session_id)
            ).scalars()
        )
        new_units: dict[str, TacticalUnit] = {}
        for u in src_units:
            clone = TacticalUnit(
                session_id=new.id,
                designation=u.designation,
                unit_level=u.unit_level,
                # 兵科**不只是圖示**：ENGINEER 決定破障/設障令下不下得了、雷區通過機率、
                # 障礙通過速度。漏掉它，副本裡的工兵連會退回 UNKNOWN 而失去全部工兵能力
                # ——而地圖上只是符號從工兵變成通用框，很難聯想到「為什麼破不了障」。
                branch=u.branch,
                faction=u.faction,
                is_fixed=u.is_fixed,
                attributes=dict(u.attributes or {}),
                current_lat=u.current_lat,
                current_lng=u.current_lng,
                elevation=u.elevation,
                authorized_strength=u.authorized_strength,
                current_strength=u.current_strength,
                personnel_authorized=u.personnel_authorized,
                personnel_current=u.personnel_current,
                health_status=u.health_status,
                comms_status=u.comms_status,
            )
            self._db.add(clone)
            new_units[u.id] = clone
        self._db.flush()  # 取得新單位 id
        old_to_new = {old: clone.id for old, clone in new_units.items()}
        for u in src_units:
            if u.parent_id is not None and u.parent_id in new_units:
                new_units[u.id].parent_id = new_units[u.parent_id].id

        # 裝備 verbatim（templateId + 數量 + current_state）；含彈藥，開打前複製＝滿彈。
        if new_units:
            for e in self._db.execute(
                select(EquipmentInstance).where(EquipmentInstance.owner_id.in_(list(new_units)))
            ).scalars():
                self._db.add(
                    EquipmentInstance(
                        template_id=e.template_id,
                        owner_id=new_units[e.owner_id].id,
                        current_state=dict(e.current_state or {}),
                        quantity=e.quantity,
                    )
                )

        # 地圖標註/工事（設置的據點/障礙/控制措施）。
        for mf in self._db.execute(
            select(MapFeature).where(MapFeature.session_id == session_id)
        ).scalars():
            self._db.add(
                MapFeature(
                    session_id=new.id,
                    kind=mf.kind,
                    geometry_type=mf.geometry_type,
                    geometry=mf.geometry,
                    owner_faction=mf.owner_faction,
                    label=mf.label,
                    influence_radius_m=mf.influence_radius_m,
                    weapon_template_id=mf.weapon_template_id,
                    attributes=dict(mf.attributes or {}),
                )
            )

        # 參與者名冊：複製人類參與者（跳過 AI `ai-*`）；unit_scope 依 old→new 重寫、丟棄已不存在者。
        rows = list(
            self._db.execute(
                select(SessionParticipant, User)
                .join(User, User.id == SessionParticipant.user_id)
                .where(SessionParticipant.session_id == session_id)
            )
        )
        copied_user_ids: set[str] = set()
        for part, puser in rows:
            if puser.username.startswith("ai-"):
                continue  # AI issuer participant 由 orchestrator 於 runner 起跑時重建
            old_scope = part.unit_scope if isinstance(part.unit_scope, list) else []
            new_scope = [old_to_new[str(x)] for x in old_scope if str(x) in old_to_new]
            self._db.add(
                SessionParticipant(
                    user_id=part.user_id,
                    session_id=new.id,
                    faction=part.faction,
                    role=part.role,
                    unit_scope=new_scope,
                )
            )
            copied_user_ids.add(part.user_id)
        # 確保複製者為新局參與者（統裁）——即便其非來源局參與者（全知角色可跨局操作）。
        if user.id not in copied_user_ids:
            self._db.add(
                SessionParticipant(
                    user_id=user.id,
                    session_id=new.id,
                    faction=WHITE_CELL,
                    role=UserRole.EXERCISE_DIRECTOR,
                    unit_scope=[],
                )
            )

        self._db.commit()
        my_faction = self._participant_factions(user.id).get(new.id)
        return self._summary(new, my_faction, orbat_edit=True)

    def _participant_factions(self, user_id: str) -> dict[str, str]:
        rows = (
            self._db.execute(
                select(SessionParticipant).where(SessionParticipant.user_id == user_id)
            )
            .scalars()
            .all()
        )
        return {p.session_id: p.faction for p in rows}

    def _participant_seats(self, user_id: str) -> dict[str, str]:
        """呼叫者於各 session 的席位（WP-B5.2）。未指派席位者不列（前端視為 null）。"""
        rows = (
            self._db.execute(
                select(SessionParticipant).where(SessionParticipant.user_id == user_id)
            )
            .scalars()
            .all()
        )
        return {p.session_id: p.seat_role.value for p in rows if p.seat_role is not None}

    def _participant_scopes(self, user_id: str) -> dict[str, list[str]]:
        """呼叫者於各 session 的 unit_scope（限指揮單位子集；空＝整個陣營）。"""
        rows = (
            self._db.execute(
                select(SessionParticipant).where(SessionParticipant.user_id == user_id)
            )
            .scalars()
            .all()
        )
        return {
            p.session_id: [str(x) for x in p.unit_scope]
            for p in rows
            if isinstance(p.unit_scope, list) and p.unit_scope
        }

    @staticmethod
    def _summary(
        session: WargameSession,
        my_faction: str | None,
        orbat_edit: bool = False,
        my_unit_scope: list[str] | None = None,
        my_seat_role: str | None = None,
    ) -> SessionSummary:
        return SessionSummary(
            id=session.id,
            name=session.name,
            scenario_id=session.scenario_id,
            mode=session.mode.value,
            status=(
                "ARCHIVED"
                if session.archived_at is not None
                else "ENDED"
                if session.end_time is not None
                else "ACTIVE"
            ),
            my_faction=my_faction,
            exercise_id=session.exercise_id,
            session_role=session.session_role.value if session.session_role else None,
            orbat_edit=orbat_edit,
            allow_fratricide=bool(session.allow_fratricide),
            my_unit_scope=my_unit_scope or [],
            my_seat_role=my_seat_role,
            my_allowed_order_types=_allowed_order_types(my_seat_role),
            archived_at=(
                session.archived_at.isoformat()
                if session.archived_at is not None and hasattr(session.archived_at, "isoformat")
                else (str(session.archived_at) if session.archived_at else None)
            ),
            start_time=(
                session.start_time.isoformat()
                if hasattr(session.start_time, "isoformat")
                else (str(session.start_time) if session.start_time else None)
            ),
            world_start_time=(
                session.world_start_time.isoformat()
                if session.world_start_time is not None
                and hasattr(session.world_start_time, "isoformat")
                else (str(session.world_start_time) if session.world_start_time else None)
            ),
        )

    def edit_session(
        self, user: CurrentUser, session_id: str, req: EditSessionRequest
    ) -> SessionSummary:
        """編輯已開推演設定（#16）——名稱 / 想定世界初始日期時間。限統裁/管理（全知）。"""
        from datetime import datetime

        from app.errors import AuthForbiddenError, SessionNotFoundError

        # 全知（統裁/白軍/管理）恆可編；否則須為本 session 的統裁/白軍參與者（含建立者）。
        if user.role not in _OMNISCIENT_ROLES:
            part = self._db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.user_id == user.id,
                    SessionParticipant.session_id == session_id,
                )
            ).scalar_one_or_none()
            if part is None or part.role not in (
                UserRole.EXERCISE_DIRECTOR,
                UserRole.WHITE_CELL_STAFF,
            ):
                raise AuthForbiddenError("僅統裁/管理可編輯推演設定")
        session = self._db.get(WargameSession, session_id)
        if session is None:
            raise SessionNotFoundError(f"session 不存在：{session_id}")
        if req.name is not None:
            session.name = req.name
        if req.world_start_time is not None:
            wst = req.world_start_time.strip()
            if wst == "":
                session.world_start_time = None
            else:
                try:
                    session.world_start_time = datetime.fromisoformat(wst)  # type: ignore[assignment]
                except ValueError as exc:
                    raise AuthForbiddenError(f"世界初始時間格式錯誤：{wst}") from exc
        self._db.commit()
        my_faction = self._participant_factions(user.id).get(session_id)
        return self._summary(session, my_faction, orbat_edit=True)

    # ---------------- #31 封存 / 還原 / 刪除 ----------------

    def _require_director(self, user: CurrentUser, session_id: str) -> WargameSession:
        """限統裁/管理（全知），否則須為本 session 統裁/白軍參與者。回傳 session（不存在→404）。"""
        from app.errors import AuthForbiddenError, SessionNotFoundError

        if user.role not in _OMNISCIENT_ROLES:
            part = self._db.execute(
                select(SessionParticipant).where(
                    SessionParticipant.user_id == user.id,
                    SessionParticipant.session_id == session_id,
                )
            ).scalar_one_or_none()
            if part is None or part.role not in (
                UserRole.EXERCISE_DIRECTOR,
                UserRole.WHITE_CELL_STAFF,
            ):
                raise AuthForbiddenError("僅統裁/管理可封存或刪除推演")
        session = self._db.get(WargameSession, session_id)
        if session is None:
            raise SessionNotFoundError(f"session 不存在：{session_id}")
        return session

    def set_archived(self, user: CurrentUser, session_id: str, archived: bool) -> SessionSummary:
        """封存（archived=True）或還原（False）一局。封存＝活模擬凍結、移入歷史頁。"""
        from datetime import datetime

        session = self._require_director(user, session_id)
        # 封存時間為真實世界 metadata（非模擬邏輯，不受 SimClock 紅線約束）。
        session.archived_at = datetime.now() if archived else None  # type: ignore[assignment]
        self._db.commit()
        my_faction = self._participant_factions(user.id).get(session_id)
        return self._summary(session, my_faction, orbat_edit=True)

    def delete_session(self, user: CurrentUser, session_id: str) -> None:
        """永久刪除一局（連同所有子表）。限統裁/管理，前端須二次確認。

        多數子表（單位/事件/指令/參與者/檢查點/情報/AI 記錄/AAR）未設 DB 級 onDelete cascade，
        直接刪 session 會觸發 FK 違反 → 500。故此依 FK 安全順序先清子表再刪 session；
        EquipmentInstance / 單位階層由 TacticalUnit 的 ondelete=CASCADE 一併帶走。

        ## 為什麼要重試

        刪一局**進行中**的推演，會和它自己的 runner 搶同一批列：偵測 sweep 每 tick 都在
        改寫 `IntelContact`，於是 MariaDB 丟 1020「Record has changed since last read」。
        呼叫端（API 層）已先設收場旗標讓 runner 停下，但旗標是輪詢的——中間有個窗口。
        使用者看到的症狀是「刪除失敗」、再按一次就成功了，那是最糟的一種錯誤：
        看起來像隨機故障，實際上有明確成因。
        """
        self._require_director(user, session_id)
        for attempt in range(_DELETE_ATTEMPTS):
            try:
                purge_session_rows(self._db, session_id)
                self._db.commit()
                return
            except OperationalError:
                self._db.rollback()
                if attempt == _DELETE_ATTEMPTS - 1:
                    raise
                time.sleep(_DELETE_BACKOFF_S)


def _copy_json(value: object) -> object:
    """深拷貝 JSON 欄位（list/dict 各拷一層即可——內容是純資料）。

    **不能直接指派**：那會讓副本與原局共用同一個 Python 物件，改一邊動兩邊。
    """
    if isinstance(value, list):
        return list(value)
    if isinstance(value, dict):
        return dict(value)
    return value


def _derive_seed(name: str, user_id: str, session_id: str) -> int:
    """由建局名 + 建立者 + session.id 確定性導出 master_seed（避免裸 random；P4 模擬 RNG 根）。

    摻入 session.id（uuid）確保即使同名同人建多局，master_seed 也互異（CODE_REVIEW C15）。
    以 BLAKE2b 取 63-bit 正整數，落在 DB BigInt 範圍內。
    """
    digest = hashlib.blake2b(f"{name}:{user_id}:{session_id}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big") & 0x7FFF_FFFF_FFFF_FFFF


def _allowed_order_types(seat_role: str | None) -> list[str]:
    """席位 → 可下令型別。

    ⚠ **未指派（None）與唯讀席位（情報官/觀察員）都回空 list**——兩者的差別由
    `my_seat_role` 本身表達，消費端要一起讀。這裡不另造一個 sentinel：
    「沒有席位」與「這個席位不能下令」在資料上本來就是兩個欄位的事。

    投影 `seats.SEAT_ORDER_TYPES` 而不是讓前端照席位名自己查表：那張表已經被漏改過兩次
    （作戰官少了 MISSION/POSTURE/FORMATION/ENGINEER、後勤官少了 RESUPPLY），
    前端再抄一份就是第三份會漂開的複本，而症狀是「下拉裡看得到的令送出去被擋」。

    排序固定（依 `OrderType` 宣告順序）——回應體的欄位順序不該隨 set 的雜湊擺動。
    """
    from app.models.enums import SeatRole
    from app.orders.schemas import OrderType
    from app.seats import SEAT_ORDER_TYPES

    if not seat_role:
        return []
    try:
        seat = SeatRole(seat_role)
    except ValueError:
        return []  # 不認得的席位字串：當作未指派，交還角色規則判斷
    allowed = SEAT_ORDER_TYPES.get(seat, frozenset())
    return [t.value for t in OrderType if t in allowed]
