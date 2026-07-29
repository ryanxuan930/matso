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


class SeatRole(enum.StrEnum):
    """席位（WP-B5.1，[JCATS-F p.9–10]）——同一陣營內的參謀席次。

    與 `UserRole` **正交**：UserRole 說「這個帳號在系統裡是誰」，
    SeatRole 說「他在這一局坐哪個位子」。
    參與者的 seat_role **可為 None＝未指派**，此時權限完全沿用 UserRole 的既有規則。
    """

    COMMANDER = "COMMANDER"
    S2_INTEL = "S2_INTEL"
    S3_OPS = "S3_OPS"
    FSO_FIRES = "FSO_FIRES"
    S4_LOG = "S4_LOG"
    OBSERVER = "OBSERVER"


class MessageKind(enum.StrEnum):
    """信文種類（WP-B5.2）。REQUEST/APPROVAL 會帶 ref_id 指向申請單。"""

    FREE_TEXT = "FREE_TEXT"
    REQUEST = "REQUEST"
    APPROVAL = "APPROVAL"
    REPORT = "REPORT"


class RequestKind(enum.StrEnum):
    """申請單種類（WP-B5.2，[JCATS-A p.13,15,26]）。"""

    AIR_RECON = "AIR_RECON"
    FIRE_SUPPORT = "FIRE_SUPPORT"
    RESUPPLY_VOUCHER = "RESUPPLY_VOUCHER"
    # 臨機火力申請（WP-C10.1）。與 FIRE_SUPPORT 分開：後者是「解鎖一次曲射任務」的授權，
    # 這個是「我看到目標、請對這裡射擊」的具體任務單（帶目標座標，且須有觀測）。
    CALL_FOR_FIRE = "CALL_FOR_FIRE"


class RequestStatus(enum.StrEnum):
    """PENDING →（核覆）→ APPROVED / DENIED；APPROVED →（用掉）→ EXPENDED（終態）。

    **「已核准」與「還沒用掉」是兩件事**——合併成一個狀態會讓一張核准單被用兩次。
    """

    PENDING = "PENDING"
    APPROVED = "APPROVED"
    DENIED = "DENIED"
    EXPENDED = "EXPENDED"


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


class FirePlanStatus(enum.StrEnum):
    """火力計畫狀態（WP-C10.3）。取消時其未執行目標一律轉 SKIPPED。"""

    ACTIVE = "ACTIVE"
    CANCELLED = "CANCELLED"


class FireSchedule(enum.StrEnum):
    """預劃目標的射擊時機（WP-C10.3）。"""

    AT_TICK = "AT_TICK"  # 到指定 tick 自動下令（攻擊準備射擊）
    ON_CALL = "ON_CALL"  # 待命，由 FSO 席位呼叫


class FirePlanTargetStatus(enum.StrEnum):
    """預劃目標狀態（WP-C10.3）。

    **`FIRED` 只代表「令送出去了」**，不代表打中——裁決失敗（無彈/超射程）的令會以
    零毀傷 COMPLETED，那是帳本上的事實，不回頭改這裡的狀態。想知道打中沒有要看帳本。
    """

    PENDING = "PENDING"
    FIRED = "FIRED"
    FAILED = "FAILED"  # 下令被擋（驗證/預檢/火協），原因見 failure_reason
    SKIPPED = "SKIPPED"  # 計畫被取消時尚未執行者
