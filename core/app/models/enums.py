"""Python enums — 名稱與值 MUST 與 db/prisma/schema.prisma 的 enum 完全一致。"""

import enum


class SessionMode(enum.StrEnum):
    REALTIME = "REALTIME"
    WEGO = "WEGO"
    IGO_UGO = "IGO_UGO"


class UnitLevel(enum.StrEnum):
    """編制層級。

    ⚠⚠ **宣告順序就是大小順序（大 → 小），不是隨手排的。**
    `aggregate.py` 與 `engine/comms.py` 都用 `enumerate(UnitLevel)` 當「編制大小」的秩：
    `_SIZE_RANK = {level: rank for rank, level in enumerate(UnitLevel)}`，
    再以 `rank <= _SIZE_RANK[BATTALION]` 判斷「營級以上＝指揮節點」。
    **在尾端追加新值（最自然的做法）會讓那個值變成比 INDIVIDUAL 還小**，
    而且不會有任何錯誤——只會讓聚合門檻與通信指揮節點的判定悄悄跑掉。
    新增層級一律插進正確的大小位置；`test_unit_level_order.py` 釘住這件事。
    """

    THEATER = "THEATER"
    ARMY_GROUP = "ARMY_GROUP"
    ARMY = "ARMY"
    CORPS = "CORPS"
    DIVISION = "DIVISION"
    BRIGADE = "BRIGADE"
    REGIMENT = "REGIMENT"
    BATTALION = "BATTALION"
    COMPANY = "COMPANY"
    PLATOON = "PLATOON"
    SECTION = "SECTION"
    SQUAD = "SQUAD"
    FIRETEAM = "FIRETEAM"
    INDIVIDUAL = "INDIVIDUAL"


class UnitBranch(enum.StrEnum):
    """兵科——APP-6A/2525C function ID 的來源（決定符號畫成步兵斜線/裝甲橢圓/砲兵圓點…）。

    每一個值都實測過 milsymbol 會畫出**獨特**的圖示，不是只是 `isValid()` 為真而已
    （有些代碼合法但畫出來與通用框一模一樣）。

    `UNKNOWN` 是**中性預設**：對應通用框 `U-----`，也就是不指定兵科的單位外觀完全不變。
    既有想定因此零影響。
    """

    UNKNOWN = "UNKNOWN"
    INFANTRY = "INFANTRY"
    ARMOR = "ARMOR"
    RECON = "RECON"
    ARTILLERY = "ARTILLERY"
    AIR_DEFENSE = "AIR_DEFENSE"
    ENGINEER = "ENGINEER"
    MISSILE = "MISSILE"
    AVIATION = "AVIATION"
    SIGNAL = "SIGNAL"
    INTEL = "INTEL"
    SUPPLY = "SUPPLY"
    MEDICAL = "MEDICAL"
    MAINTENANCE = "MAINTENANCE"
    TRANSPORT = "TRANSPORT"
    HQ = "HQ"


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


class ExercisePhase(enum.StrEnum):
    """演習階段（WP-B1，對映 [JCATS-A] 的 17 步 SOP）。

    **只能沿序前進、一次一階**——倒退會讓已經簽證的參數（WP-B4）與稽核軌跡失去意義。
    """

    PREP = "PREP"  # 整備（會議、想定發佈、飽和測試）
    REHEARSAL = "REHEARSAL"  # 預推
    EXECUTION = "EXECUTION"  # 正式實施（WP-B4 於此階段簽證鎖定參數）
    REVIEW = "REVIEW"  # 檢討
    ARCHIVED = "ARCHIVED"  # 撤收建檔


class SessionRole(enum.StrEnum):
    """一局在演習中的角色（WP-B1）。NULL＝未指定（含獨立局）。"""

    REHEARSAL = "REHEARSAL"
    MAIN = "MAIN"
    ANALYSIS = "ANALYSIS"
