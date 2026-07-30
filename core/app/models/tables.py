"""SQLAlchemy table 定義 — 逐欄對應 db/prisma/schema.prisma。

慣例：Python 屬性用 snake_case，實際欄位名（第一個參數）保持 prisma 的 camelCase。
只在 prisma 有 @relation 的欄位加 ForeignKey，忠實反映 DB 實際約束。
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    Double,
    ForeignKey,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base
from app.models.enums import (
    CommsState,
    ExercisePhase,
    FirePlanStatus,
    FirePlanTargetStatus,
    FireSchedule,
    IntelFidelity,
    MessageKind,
    OrderStatus,
    RequestKind,
    RequestStatus,
    SeatRole,
    SessionMode,
    SessionRole,
    UnitBranch,
    UnitLevel,
    UserRole,
)


def _uuid() -> str:
    return str(uuid.uuid4())


class SystemConfiguration(Base):
    __tablename__ = "SystemConfiguration"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    version_name: Mapped[str] = mapped_column("versionName", String(191))
    sim_tick_rate_ms: Mapped[int] = mapped_column("simTickRateMs", Integer, default=1000)
    global_rules: Mapped[dict] = mapped_column("globalRules", JSON)  # type: ignore[type-arg]
    integration_config: Mapped[dict] = mapped_column("integrationConfig", JSON)  # type: ignore[type-arg]
    updated_at: Mapped[str] = mapped_column("updatedAt", DateTime(timezone=False))


class WargameSession(Base):
    __tablename__ = "WargameSession"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column("name", String(191))
    scenario_id: Mapped[str | None] = mapped_column("scenarioId", String(191))
    master_seed: Mapped[int] = mapped_column("masterSeed", BigInteger)
    mode: Mapped[SessionMode] = mapped_column(
        "mode", SAEnum(SessionMode), default=SessionMode.REALTIME
    )
    start_time: Mapped[str] = mapped_column(
        "startTime", DateTime(timezone=False), server_default=func.now()
    )
    end_time: Mapped[str | None] = mapped_column("endTime", DateTime(timezone=False))
    # #31 封存時間（歷史頁）：有值＝已封存（活模擬凍結、於歷史頁可還原/刪除）。
    archived_at: Mapped[str | None] = mapped_column("archivedAt", DateTime(timezone=False))
    # 想定世界初始日期時間（in-world t=0；供 #6 日照推算，可編輯 #16）。
    world_start_time: Mapped[str | None] = mapped_column("worldStartTime", DateTime(timezone=False))
    current_weather: Mapped[dict] = mapped_column("currentWeather", JSON)  # type: ignore[type-arg]
    # #6：允許自行編輯本軍編裝的陣營清單（White Cell 設定）。None = 僅白軍可編。
    # 申請單配額快照（WP-B5.2）：開局時從想定複製，不即時讀想定——想定可能在演習中被
    # 編修或刪除，即時讀會讓已開的局配額被追溯改掉。None＝未宣告＝不限（既有局語義）。
    request_quotas: Mapped[dict | None] = mapped_column("requestQuotas", JSON)  # type: ignore[type-arg]
    # 曲射火協（WP-B5.3）：開局快照。None/False＝不設限（既有局零變更）。
    indirect_fire_requires_approval: Mapped[bool | None] = mapped_column(
        "indirectFireRequiresApproval", Boolean
    )
    # WP-C9：NULL＝未宣告＝維持既有「非敵對一律拒」。**不可設 NOT NULL + default**，
    # 那會回頭改掉每一個進行中的既有局的語義。
    allow_fratricide: Mapped[bool | None] = mapped_column("allowFratricide", Boolean)
    # 聚合裁決門檻（此級以上走 Lanchester）。**None＝BATTALION**（既有預設）。
    aggregate_adjudication_level: Mapped[UnitLevel | None] = mapped_column(
        "aggregateAdjudicationLevel", SAEnum(UnitLevel)
    )
    # WP-C4a：NULL＝未宣告＝整場白天（既有局語義）。
    day_night: Mapped[dict | None] = mapped_column("dayNight", JSON, nullable=True)  # type: ignore[type-arg]
    # 本局的陣營顏色/顯示名（想定 `factions[]` 開局快照）。**None＝未宣告＝前端預設**。
    faction_colors: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "factionColors", JSON, nullable=True
    )
    faction_display_names: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "factionDisplayNames", JSON, nullable=True
    )
    # 本局的勝負條件（想定 `victory_conditions` 開局快照）。**None＝未宣告＝最後存活**。
    victory_conditions: Mapped[list | None] = mapped_column(  # type: ignore[type-arg]
        "victoryConditions", JSON, nullable=True
    )
    # 該局的 tick 長度（ms 模擬時間），開局從想定快照。**None＝沿用系統設定**。
    # 想定 schema 一直把 tick_rate_ms 列為必填、loader 也讀得進來，但它過去只被
    # `dump.py` 拿去做 roundtrip 匯出——沒有任何一條路把它帶進執行期。
    tick_rate_ms: Mapped[int | None] = mapped_column("tickRateMs", Integer, nullable=True)
    orbat_edit_factions: Mapped[list | None] = mapped_column(  # type: ignore[type-arg]
        "orbatEditFactions", JSON, nullable=True
    )
    # #98 陣營關係矩陣：三元組 [[a, b, "ALLIED"|"NEUTRAL"|"HOSTILE"], …]，對稱。
    # None = 未宣告 → 全 HOSTILE 預設（既有局零遷移）。以 `factions.relations_from_triples` 讀取。
    faction_relations: Mapped[list | None] = mapped_column(  # type: ignore[type-arg]
        "factionRelations", JSON, nullable=True
    )
    # WP-A3 禁射區宣告（NULL＝無禁射區，既有局零遷移）。格集由 orders/no_strike.py 導出。
    no_strike_zones: Mapped[list | None] = mapped_column(  # type: ignore[type-arg]
        "noStrikeZones", JSON, nullable=True
    )
    # WP-B6 想定交戰規則宣告（NULL＝無限制，既有局零遷移）。解析見 orders/roe.py。
    roe: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "roe", JSON, nullable=True
    )
    # WP-B6 想定機動覆寫（NULL＝用出貨預設）。合併見 movement/mobility_matrix.MobilityRules。
    mobility_overrides: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "mobilityOverrides", JSON, nullable=True
    )
    # WP-C10.5 陣地變換（NULL＝停用，既有局零遷移）。解析見 fires/survivability.py。
    survivability_move: Mapped[dict | None] = mapped_column(  # type: ignore[type-arg]
        "survivabilityMove", JSON, nullable=True
    )
    # WP-B2 MSEL 腳本事件（NULL＝無 MSEL）。**在此之前想定的 msel 從未被持久化**：
    # 載得進來、卻沒有任何一條路把它帶進執行期，等於整個 MSEL 子系統是死的。
    msel: Mapped[list | None] = mapped_column("msel", JSON, nullable=True)  # type: ignore[type-arg]
    # WP-B1 所屬演習（NULL＝獨立局，既有局零遷移）。刻意無 FK——刪演習不連坐刪局。
    exercise_id: Mapped[str | None] = mapped_column("exerciseId", String(191))
    session_role: Mapped[SessionRole | None] = mapped_column("sessionRole", SAEnum(SessionRole))


class TacticalUnit(Base):
    __tablename__ = "TacticalUnit"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        "sessionId", String(191), ForeignKey("WargameSession.id")
    )
    designation: Mapped[str] = mapped_column("designation", String(191))
    unit_level: Mapped[UnitLevel] = mapped_column("unitLevel", SAEnum(UnitLevel))
    # 兵科：地圖符號的 2525C function ID 來源。DB 為 NOT NULL DEFAULT 'UNKNOWN'，
    # 既有列全部拿到 UNKNOWN → 對應通用框，符號外觀不變。
    branch: Mapped[UnitBranch] = mapped_column(
        "branch", SAEnum(UnitBranch), default=UnitBranch.UNKNOWN, server_default="UNKNOWN"
    )
    # faction＝想定定義字串 id（SPEC §12.1/ADR 006）；驗證於 app.factions
    faction: Mapped[str] = mapped_column("faction", String(191))
    parent_id: Mapped[str | None] = mapped_column(
        "parentId", String(191), ForeignKey("TacticalUnit.id", ondelete="CASCADE")
    )
    # 固定單位（指揮部/後勤/陣地）：不接受 MOVE 令、不被派去移動（劇本 ORBAT 設定，唯讀跟隨）。
    is_fixed: Mapped[bool] = mapped_column("isFixed", Boolean, default=False)
    attributes: Mapped[dict] = mapped_column("attributes", JSON, default=dict)  # type: ignore[type-arg]
    current_lat: Mapped[float | None] = mapped_column("currentLat", Double)
    current_lng: Mapped[float | None] = mapped_column("currentLng", Double)
    elevation: Mapped[float | None] = mapped_column("elevation", Double)
    # 戰力（真實化交戰）：authorized＝滿編（分母，不遞減）；current＝當前權威戰力（交戰扣此）；
    # health_status 改為由 current/authorized 導出的效能%（顯示用）。人員數供顯示/回報（可空）。
    authorized_strength: Mapped[float] = mapped_column("authorizedStrength", Double, default=100.0)
    current_strength: Mapped[float] = mapped_column("currentStrength", Double, default=100.0)
    personnel_authorized: Mapped[int | None] = mapped_column("personnelAuthorized", Integer)
    personnel_current: Mapped[int | None] = mapped_column("personnelCurrent", Integer)
    health_status: Mapped[float] = mapped_column("healthStatus", Double, default=100.0)
    comms_status: Mapped[CommsState] = mapped_column(
        "commsStatus", SAEnum(CommsState), default=CommsState.ONLINE
    )


class EquipmentTemplate(Base):
    __tablename__ = "EquipmentTemplate"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column("name", String(191))
    category: Mapped[str] = mapped_column("category", String(191))
    base_stats: Mapped[dict] = mapped_column("baseStats", JSON, default=dict)  # type: ignore[type-arg]


class EquipmentInstance(Base):
    __tablename__ = "EquipmentInstance"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    template_id: Mapped[str] = mapped_column(
        "templateId", String(191), ForeignKey("EquipmentTemplate.id")
    )
    owner_id: Mapped[str] = mapped_column(
        "ownerId", String(191), ForeignKey("TacticalUnit.id", ondelete="CASCADE")
    )
    current_state: Mapped[dict] = mapped_column("currentState", JSON, default=dict)  # type: ignore[type-arg]
    # #30 建制數量：一個 instance 代表 N 件同型裝備（如班內 7 支步槍）；驅動 squad 火力容量。
    quantity: Mapped[int] = mapped_column("quantity", Integer, default=1)


class MapFeature(Base):
    """地圖標註/工事：武器據點、障礙、建築、控制措施（點/線/面 + 影響範圍 + 屬性）。"""

    __tablename__ = "MapFeature"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        "sessionId", String(191), ForeignKey("WargameSession.id", ondelete="CASCADE")
    )
    kind: Mapped[str] = mapped_column("kind", String(191))
    geometry_type: Mapped[str] = mapped_column("geometryType", String(191))
    # GeoJSON coordinates（依 geometryType：Point/Line/Polygon）
    geometry: Mapped[Any] = mapped_column("geometry", JSON)
    owner_faction: Mapped[str] = mapped_column("ownerFaction", String(191))
    label: Mapped[str | None] = mapped_column("label", String(191), nullable=True)
    influence_radius_m: Mapped[float | None] = mapped_column("influenceRadiusM", Double)
    weapon_template_id: Mapped[str | None] = mapped_column("weaponTemplateId", String(191))
    attributes: Mapped[dict] = mapped_column("attributes", JSON, default=dict)  # type: ignore[type-arg]
    created_at: Mapped[str] = mapped_column("createdAt", DateTime, server_default=func.now())


class TacticalEventLog(Base):
    __tablename__ = "TacticalEventLog"
    __table_args__ = (UniqueConstraint("sessionId", "seq"),)

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        "sessionId", String(191), ForeignKey("WargameSession.id")
    )
    seq: Mapped[int] = mapped_column("seq", Integer)
    tick: Mapped[int] = mapped_column("tick", Integer)
    timestamp: Mapped[str] = mapped_column(
        "timestamp", DateTime(timezone=False), server_default=func.now()
    )
    event_type: Mapped[str] = mapped_column("eventType", String(191))
    initiator_id: Mapped[str | None] = mapped_column(
        "initiatorId", String(191), ForeignKey("TacticalUnit.id")
    )
    target_id: Mapped[str | None] = mapped_column(
        "targetId", String(191), ForeignKey("TacticalUnit.id")
    )
    weather_snapshot: Mapped[dict] = mapped_column("weatherSnapshot", JSON)  # type: ignore[type-arg]
    terrain_modifier: Mapped[float] = mapped_column("terrainModifier", Double)
    reasoning_chain: Mapped[str | None] = mapped_column("reasoningChain", Text)
    ai_decision: Mapped[dict] = mapped_column("aiDecision", JSON)  # type: ignore[type-arg]
    damage_calc: Mapped[float | None] = mapped_column("damageCalc", Double)
    # 非證據性診斷；刻意不入 hash chain（可含牆鐘等非決定性值，見 ledger.py）
    detail: Mapped[dict | None] = mapped_column("detail", JSON)  # type: ignore[type-arg]
    prev_hash: Mapped[str] = mapped_column("prevHash", String(191))
    self_hash: Mapped[str] = mapped_column("selfHash", String(191))


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column("username", String(191), unique=True)
    password_hash: Mapped[str] = mapped_column("passwordHash", String(191))
    totp_secret: Mapped[str | None] = mapped_column("totpSecret", String(191))
    role: Mapped[UserRole] = mapped_column("role", SAEnum(UserRole))
    # WP-E2 帳號鎖定（防爆破）。NULL/0 ＝從未失敗過（既有列的語義）。
    failed_attempts: Mapped[int | None] = mapped_column("failedAttempts", Integer)
    locked_until: Mapped[datetime | None] = mapped_column("lockedUntil", DateTime, nullable=True)
    created_at: Mapped[str] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class RevokedToken(Base):
    """已撤銷的 refresh token（WP-E2）。

    以 `jti` 為鍵。**過期的列可以安全清掉**——token 本身也過期了，留著只是佔位。
    `reason` 供稽核：是正常登出、輪替汰換，還是偵測到重用（可能被竊）。
    """

    __tablename__ = "RevokedToken"

    jti: Mapped[str] = mapped_column("jti", String(191), primary_key=True)
    user_id: Mapped[str] = mapped_column("userId", String(191))
    expires_at: Mapped[datetime] = mapped_column("expiresAt", DateTime)
    reason: Mapped[str] = mapped_column("reason", String(191))
    revoked_at: Mapped[str] = mapped_column(
        "revokedAt", DateTime(timezone=False), server_default=func.now()
    )


class SessionParticipant(Base):
    __tablename__ = "SessionParticipant"
    __table_args__ = (UniqueConstraint("userId", "sessionId"),)

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column("userId", String(191), ForeignKey("User.id"))
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    # faction＝想定定義字串 id（SPEC §12.1/ADR 006）；驗證於 app.factions
    faction: Mapped[str] = mapped_column("faction", String(191))
    role: Mapped[UserRole] = mapped_column("role", SAEnum(UserRole))
    # 席位（WP-B5.1）：同陣營內的參謀分工，與 role 正交。
    # **None＝未指派席位 → 權限沿用 role 既有規則**（既有局零行為變更）。
    seat_role: Mapped[SeatRole | None] = mapped_column("seatRole", SAEnum(SeatRole))
    # unit_scope＝限指揮之單位 id 清單（JSON 陣列；空＝整個陣營）。以 Any 容納 JSON 值。
    unit_scope: Mapped[Any] = mapped_column("unitScope", JSON, default=list)


class Message(Base):
    """C2 信文（WP-B5.2）——受眾＝ to_seat（指定席位）或 to_faction（整個陣營）。"""

    __tablename__ = "Message"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    kind: Mapped[MessageKind] = mapped_column("kind", SAEnum(MessageKind))
    from_user_id: Mapped[str] = mapped_column("fromUserId", String(191))
    from_seat: Mapped[SeatRole | None] = mapped_column("fromSeat", SAEnum(SeatRole))
    to_seat: Mapped[SeatRole | None] = mapped_column("toSeat", SAEnum(SeatRole))
    to_faction: Mapped[str] = mapped_column("toFaction", String(191))
    ref_id: Mapped[str | None] = mapped_column("refId", String(191))
    body: Mapped[str] = mapped_column("body", Text)
    tick: Mapped[int] = mapped_column("tick", Integer)
    read_at: Mapped[Any | None] = mapped_column("readAt", DateTime(timezone=False))
    created_at: Mapped[Any] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class Request(Base):
    """申請單（WP-B5.2）——核覆留痕供 AAR 重建事件鏈。"""

    __tablename__ = "Request"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    faction: Mapped[str] = mapped_column("faction", String(191))
    kind: Mapped[RequestKind] = mapped_column("kind", SAEnum(RequestKind))
    status: Mapped[RequestStatus] = mapped_column(
        "status", SAEnum(RequestStatus), default=RequestStatus.PENDING
    )
    params: Mapped[Any] = mapped_column("params", JSON, default=dict)
    requested_by_id: Mapped[str] = mapped_column("requestedById", String(191))
    requested_seat: Mapped[SeatRole | None] = mapped_column("requestedSeat", SAEnum(SeatRole))
    requested_at_tick: Mapped[int] = mapped_column("requestedAtTick", Integer)
    decided_by_id: Mapped[str | None] = mapped_column("decidedById", String(191))
    decided_at_tick: Mapped[int | None] = mapped_column("decidedAtTick", Integer)
    decision_note: Mapped[str | None] = mapped_column("decisionNote", Text)
    created_at: Mapped[Any] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class Scenario(Base):
    __tablename__ = "Scenario"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column("name", String(191))
    version: Mapped[str] = mapped_column("version", String(191))
    package_blob: Mapped[bytes] = mapped_column("packageBlob", LargeBinary)
    checksum: Mapped[str] = mapped_column("checksum", String(191))
    created_by: Mapped[str] = mapped_column("createdBy", String(191))
    created_at: Mapped[str] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class Order(Base):
    __tablename__ = "Order"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    issuer_id: Mapped[str] = mapped_column("issuerId", String(191))
    unit_id: Mapped[str] = mapped_column("unitId", String(191))
    order_type: Mapped[str] = mapped_column("orderType", String(191))
    payload: Mapped[dict] = mapped_column("payload", JSON)  # type: ignore[type-arg]
    status: Mapped[OrderStatus] = mapped_column(
        "status", SAEnum(OrderStatus), default=OrderStatus.PENDING
    )
    precheck: Mapped[dict | None] = mapped_column("precheck", JSON)  # type: ignore[type-arg]
    issued_at_tick: Mapped[int] = mapped_column("issuedAtTick", Integer)
    resolved_at_tick: Mapped[int | None] = mapped_column("resolvedAtTick", Integer)
    # WP-A2：分解自哪一道 MISSION 令。NULL＝直接下的令（既有令零遷移）。
    # 刻意無 FK——母令被硬刪時子令不該連坐消失，那些子令是既成事實。
    parent_order_id: Mapped[str | None] = mapped_column("parentOrderId", String(191))


class IntelContact(Base):
    __tablename__ = "IntelContact"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    # faction＝想定定義字串 id（SPEC §12.1/ADR 006）；驗證於 app.factions
    faction: Mapped[str] = mapped_column("faction", String(191))
    target_unit_id: Mapped[str] = mapped_column("targetUnitId", String(191))
    fidelity: Mapped[IntelFidelity] = mapped_column("fidelity", SAEnum(IntelFidelity))
    last_seen_tick: Mapped[int] = mapped_column("lastSeenTick", Integer)
    last_seen_lat: Mapped[float] = mapped_column("lastSeenLat", Double)
    last_seen_lng: Mapped[float] = mapped_column("lastSeenLng", Double)
    error_radius_m: Mapped[float] = mapped_column("errorRadiusM", Double)


class SimCheckpoint(Base):
    __tablename__ = "SimCheckpoint"
    __table_args__ = (UniqueConstraint("sessionId", "tick"),)

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    tick: Mapped[int] = mapped_column("tick", Integer)
    # 快照當下的 ledger tip seq——rollback 後 tick 非單調，seq 才是時間軸身分（O1.7/R3）
    ledger_seq: Mapped[int] = mapped_column("ledgerSeq", Integer)
    state_blob: Mapped[bytes] = mapped_column("stateBlob", LargeBinary)
    state_hash: Mapped[str] = mapped_column("stateHash", String(191))
    created_at: Mapped[str] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class AIInvocationLog(Base):
    __tablename__ = "AIInvocationLog"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str | None] = mapped_column("sessionId", String(191))
    role: Mapped[str] = mapped_column("role", String(191))
    adapter: Mapped[str] = mapped_column("adapter", String(191))
    prompt_hash: Mapped[str] = mapped_column("promptHash", String(191))
    request: Mapped[dict] = mapped_column("request", JSON)  # type: ignore[type-arg]
    response: Mapped[dict] = mapped_column("response", JSON)  # type: ignore[type-arg]
    latency_ms: Mapped[int] = mapped_column("latencyMs", Integer)
    tokens_in: Mapped[int] = mapped_column("tokensIn", Integer)
    tokens_out: Mapped[int] = mapped_column("tokensOut", Integer)
    guardrail_result: Mapped[dict] = mapped_column("guardrailResult", JSON)  # type: ignore[type-arg]
    created_at: Mapped[str] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class AARReport(Base):
    __tablename__ = "AARReport"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191), unique=True)
    narrative: Mapped[dict] = mapped_column("narrative", JSON)  # type: ignore[type-arg]
    metrics: Mapped[dict] = mapped_column("metrics", JSON)  # type: ignore[type-arg]
    generated_at: Mapped[str] = mapped_column(
        "generatedAt", DateTime(timezone=False), server_default=func.now()
    )


class PluginRegistry(Base):
    __tablename__ = "PluginRegistry"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column("name", String(191), unique=True)
    kind: Mapped[str] = mapped_column("kind", String(191))
    endpoint: Mapped[str] = mapped_column("endpoint", String(191))
    contract_ver: Mapped[str] = mapped_column("contractVer", String(191))
    health_state: Mapped[str] = mapped_column("healthState", String(191))
    config: Mapped[dict] = mapped_column("config", JSON)  # type: ignore[type-arg]
    enabled: Mapped[bool] = mapped_column("enabled", Boolean, default=True)


class FirePlan(Base):
    """火力計畫（WP-C10.3）——預劃目標清單。**陣營私有**，查詢一律在後端過濾。"""

    __tablename__ = "FirePlan"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    faction: Mapped[str] = mapped_column("faction", String(191))
    name: Mapped[str] = mapped_column("name", String(191))
    status: Mapped[FirePlanStatus] = mapped_column(
        "status", SAEnum(FirePlanStatus), default=FirePlanStatus.ACTIVE
    )
    # 建立者的 SessionParticipant.id。自動執行的令**以建立者的身分送出**——
    # 沒有「系統」這個下令者，而預劃火力的當責者本來就是寫這份計畫的人。
    created_by_participant_id: Mapped[str | None] = mapped_column(
        "createdByParticipantId", String(191)
    )
    created_at_tick: Mapped[int] = mapped_column("createdAtTick", Integer, default=0)
    created_at: Mapped[Any] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class FirePlanTarget(Base):
    """預劃目標（打座標）。執行＝下一道 FIRE_MISSION 令（WP-C10.2），不另生物理。"""

    __tablename__ = "FirePlanTarget"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    plan_id: Mapped[str] = mapped_column("planId", String(191))
    # 計畫內序號：排程器依此排序執行，**順序必須確定**才可重播。
    seq: Mapped[int] = mapped_column("seq", Integer, default=0)
    label: Mapped[str | None] = mapped_column("label", String(191))
    target_lat: Mapped[float] = mapped_column("targetLat", Double)
    target_lng: Mapped[float] = mapped_column("targetLng", Double)
    rounds: Mapped[int] = mapped_column("rounds", Integer, default=4)
    shooter_unit_id: Mapped[str] = mapped_column("shooterUnitId", String(191))
    schedule: Mapped[FireSchedule] = mapped_column(
        "schedule", SAEnum(FireSchedule), default=FireSchedule.ON_CALL
    )
    at_tick: Mapped[int | None] = mapped_column("atTick", Integer)
    # 一張核准單只兌現一次，故是逐目標欄位而非整份計畫共用。
    fire_request_id: Mapped[str | None] = mapped_column("fireRequestId", String(191))
    status: Mapped[FirePlanTargetStatus] = mapped_column(
        "status", SAEnum(FirePlanTargetStatus), default=FirePlanTargetStatus.PENDING
    )
    order_id: Mapped[str | None] = mapped_column("orderId", String(191))
    fired_at_tick: Mapped[int | None] = mapped_column("firedAtTick", Integer)
    failure_reason: Mapped[str | None] = mapped_column("failureReason", Text)


class Exercise(Base):
    """演習專案（WP-B1）——把多次預推、正式局、檢討裝在一起的容器。

    **與 session 是兩條獨立的軸**：`phase` 講演習流程走到哪；session 的 `archived_at`
    講那一局有沒有被封存。混為一談會讓局從錯的清單裡消失。
    """

    __tablename__ = "Exercise"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column("name", String(191))
    phase: Mapped[ExercisePhase] = mapped_column(
        "phase", SAEnum(ExercisePhase), default=ExercisePhase.PREP
    )
    schedule_json: Mapped[dict | None] = mapped_column("scheduleJson", JSON)  # type: ignore[type-arg]
    # 整備勾稽項。**required 且未勾就推不動階段**——只是提示的話與沒有無異。
    checklist_json: Mapped[list | None] = mapped_column("checklistJson", JSON)  # type: ignore[type-arg]
    created_by: Mapped[str] = mapped_column("createdBy", String(191))
    created_at: Mapped[Any] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )
    # 最近一次階段推進的**真實牆鐘**時間（這不是模擬時間，故不受 SimClock 紅線約束）。
    phase_changed_at: Mapped[Any | None] = mapped_column("phaseChangedAt", DateTime(timezone=False))


class ExerciseAuditLog(Base):
    """演習稽核軌跡（WP-B1）。

    **刻意不寫 TacticalEventLog**：那是 golden 會驗的雜湊鏈，而階段推進是牆鐘的、人為的、
    局外的事件——寫進鏈裡會擾動決定性重播。SPEC 說「專屬 audit 表」正是為此。
    """

    __tablename__ = "ExerciseAuditLog"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    exercise_id: Mapped[str] = mapped_column(
        "exerciseId", String(191), ForeignKey("Exercise.id", ondelete="CASCADE")
    )
    # 演習內單調遞增。**沒有它，同一秒內的兩筆稽核順序就是隨機的**——
    # 順序隨機的稽核軌跡讀不出「先勾了項目才推階段」還是反過來。
    seq: Mapped[int] = mapped_column("seq", Integer)
    at: Mapped[Any] = mapped_column("at", DateTime(timezone=False), server_default=func.now())
    actor_id: Mapped[str] = mapped_column("actorId", String(191))
    action: Mapped[str] = mapped_column("action", String(191))
    from_phase: Mapped[ExercisePhase | None] = mapped_column("fromPhase", SAEnum(ExercisePhase))
    to_phase: Mapped[ExercisePhase | None] = mapped_column("toPhase", SAEnum(ExercisePhase))
    detail: Mapped[dict | None] = mapped_column("detail", JSON)  # type: ignore[type-arg]

    __table_args__ = (UniqueConstraint("exerciseId", "seq"),)


class ParameterSeal(Base):
    """參數簽證（WP-B4）——一場演習最多一份。

    `content_hash` 是簽證當下「全域參數」的雜湊；開局比對它，不符即拒起
    （防「演習中偷改參數重啟」）。`snapshot_blob` 是同一份內容的 zstd 壓縮 canonical JSON，
    供事後查證「當時到底鎖了什麼」。
    """

    __tablename__ = "ParameterSeal"
    __table_args__ = (UniqueConstraint("exerciseId"),)

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    exercise_id: Mapped[str] = mapped_column("exerciseId", String(191))
    sealed_at: Mapped[Any] = mapped_column(
        "sealedAt", DateTime(timezone=False), server_default=func.now()
    )
    sealed_by: Mapped[str] = mapped_column("sealedBy", String(191))
    content_hash: Mapped[str] = mapped_column("contentHash", String(191))
    snapshot_blob: Mapped[bytes] = mapped_column("snapshotBlob", LargeBinary)
