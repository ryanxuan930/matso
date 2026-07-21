"""SQLAlchemy models — 唯讀跟隨 db/prisma/schema.prisma（SPEC_FULL §15.4）。

規則：
- schema 權威是 db/prisma/schema.prisma；migration 一律由 `prisma migrate` 執行。
- Python 端永不自行 migrate；本套 models 只用於讀寫既有結構。
- 一致性由 ops/tools/schema_sync_check.py 在 CI 強制（drift = CI 失敗）。
"""

from app.models.base import Base
from app.models.enums import (
    CommsState,
    IntelFidelity,
    OrderStatus,
    SessionMode,
    UnitLevel,
    UserRole,
)
from app.models.tables import (
    AARReport,
    AIInvocationLog,
    EquipmentInstance,
    EquipmentTemplate,
    IntelContact,
    Order,
    PluginRegistry,
    Scenario,
    SessionParticipant,
    SimCheckpoint,
    SystemConfiguration,
    TacticalEventLog,
    TacticalUnit,
    User,
    WargameSession,
)

__all__ = [
    "AARReport",
    "AIInvocationLog",
    "Base",
    "CommsState",
    "EquipmentInstance",
    "EquipmentTemplate",
    "IntelContact",
    "IntelFidelity",
    "Order",
    "OrderStatus",
    "PluginRegistry",
    "Scenario",
    "SessionMode",
    "SessionParticipant",
    "SimCheckpoint",
    "SystemConfiguration",
    "TacticalEventLog",
    "TacticalUnit",
    "UnitLevel",
    "User",
    "UserRole",
    "WargameSession",
]
