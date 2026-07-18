"""SQLAlchemy table 定義 — 逐欄對應 db/prisma/schema.prisma。

慣例：Python 屬性用 snake_case，實際欄位名（第一個參數）保持 prisma 的 camelCase。
只在 prisma 有 @relation 的欄位加 ForeignKey，忠實反映 DB 實際約束。
"""

import uuid

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
    Faction,
    IntelFidelity,
    OrderStatus,
    SessionMode,
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
    current_weather: Mapped[dict] = mapped_column("currentWeather", JSON)  # type: ignore[type-arg]


class TacticalUnit(Base):
    __tablename__ = "TacticalUnit"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column(
        "sessionId", String(191), ForeignKey("WargameSession.id")
    )
    designation: Mapped[str] = mapped_column("designation", String(191))
    unit_level: Mapped[UnitLevel] = mapped_column("unitLevel", SAEnum(UnitLevel))
    faction: Mapped[Faction] = mapped_column("faction", SAEnum(Faction))
    parent_id: Mapped[str | None] = mapped_column(
        "parentId", String(191), ForeignKey("TacticalUnit.id", ondelete="CASCADE")
    )
    attributes: Mapped[dict] = mapped_column("attributes", JSON, default=dict)  # type: ignore[type-arg]
    current_lat: Mapped[float | None] = mapped_column("currentLat", Double)
    current_lng: Mapped[float | None] = mapped_column("currentLng", Double)
    elevation: Mapped[float | None] = mapped_column("elevation", Double)
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
    prev_hash: Mapped[str] = mapped_column("prevHash", String(191))
    self_hash: Mapped[str] = mapped_column("selfHash", String(191))


class User(Base):
    __tablename__ = "User"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column("username", String(191), unique=True)
    password_hash: Mapped[str] = mapped_column("passwordHash", String(191))
    totp_secret: Mapped[str | None] = mapped_column("totpSecret", String(191))
    role: Mapped[UserRole] = mapped_column("role", SAEnum(UserRole))
    created_at: Mapped[str] = mapped_column(
        "createdAt", DateTime(timezone=False), server_default=func.now()
    )


class SessionParticipant(Base):
    __tablename__ = "SessionParticipant"
    __table_args__ = (UniqueConstraint("userId", "sessionId"),)

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column("userId", String(191), ForeignKey("User.id"))
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    faction: Mapped[Faction] = mapped_column("faction", SAEnum(Faction))
    role: Mapped[UserRole] = mapped_column("role", SAEnum(UserRole))
    unit_scope: Mapped[dict] = mapped_column("unitScope", JSON)  # type: ignore[type-arg]


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


class IntelContact(Base):
    __tablename__ = "IntelContact"

    id: Mapped[str] = mapped_column("id", String(191), primary_key=True, default=_uuid)
    session_id: Mapped[str] = mapped_column("sessionId", String(191))
    faction: Mapped[Faction] = mapped_column("faction", SAEnum(Faction))
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
