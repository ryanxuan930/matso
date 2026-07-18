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


class Faction(enum.StrEnum):
    BLUE = "BLUE"
    RED = "RED"
    WHITE_CELL = "WHITE_CELL"
    ALLIED = "ALLIED"


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
