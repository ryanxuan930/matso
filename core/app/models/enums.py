"""Python enums — 名稱與值 MUST 與 db/prisma/schema.prisma 的 enum 完全一致。"""

import enum


class SessionMode(enum.StrEnum):
    REALTIME = "REALTIME"
    WEGO = "WEGO"
    IGO_UGO = "IGO_UGO"


class UnitLevel(enum.StrEnum):
    THEATER = "THEATER"
    CORPS = "CORPS"
    DIVISION = "DIVISION"
    BRIGADE = "BRIGADE"
    BATTALION = "BATTALION"
    COMPANY = "COMPANY"
    PLATOON = "PLATOON"
    SQUAD = "SQUAD"
    FIRETEAM = "FIRETEAM"
    INDIVIDUAL = "INDIVIDUAL"


class CommsState(enum.StrEnum):
    ONLINE = "ONLINE"
    DEGRADED = "DEGRADED"
    OFFLINE = "OFFLINE"


# Faction 已非封閉 enum（SPEC §12.1 / ADR 006）：faction 為想定定義字串 id，
# 驗證與保留字（WHITE_CELL）見 app.factions；DB 欄位為 String。


class UserRole(enum.StrEnum):
    EXERCISE_DIRECTOR = "EXERCISE_DIRECTOR"
    WHITE_CELL_STAFF = "WHITE_CELL_STAFF"
    COMMANDER = "COMMANDER"
    STAFF = "STAFF"
    OBSERVER = "OBSERVER"
    ANALYST = "ANALYST"
    ADMIN = "ADMIN"


class OrderStatus(enum.StrEnum):
    PENDING = "PENDING"
    VALIDATED = "VALIDATED"
    EXECUTING = "EXECUTING"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"


class IntelFidelity(enum.StrEnum):
    DETECTED = "DETECTED"
    CLASSIFIED = "CLASSIFIED"
    IDENTIFIED = "IDENTIFIED"


class AiMode(enum.StrEnum):
    """AI 運作模式（SPEC_FULL §9.0）。預設 AI_OFF＝傳統兵推。

    O6.2 以此 enum + 設定預設實作；per-session 持久化欄位於 O6.5（session 驅動 AI 時）補上。
    """

    AI_OFF = "AI_OFF"  # AI 全停用，紅軍由人操作
    AI_BARE = "AI_BARE"  # AI 啟用但無 RAG，引用必空
    AI_FULL = "AI_FULL"  # 完整管線（RAG + 引用查核）
