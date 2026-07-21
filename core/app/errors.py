"""領域例外統一定義（HOW_TO §3.1）。

規則：領域錯誤一律拋此處定義的自訂例外；API 層統一轉為契約的 error code（見 app.api.errors）。
建構參數驗證（型別/範圍防呆）仍用內建 ValueError——那是程式設計錯誤，不是領域錯誤。

每個例外帶 `error_code`（對應 contracts/core_api.yaml 的 Error.code 枚舉）與 `http_status`
（API 層轉 HTTP 狀態碼用）；`details` 供結構化補充（如預檢各項結果）。
"""

from __future__ import annotations

from typing import Any


class MatsoError(Exception):
    """所有 MATSO 領域例外的基底。error_code 對應 contracts/core_api.yaml 的 Error.code。"""

    error_code = "INTERNAL_ERROR"
    http_status = 500

    def __init__(self, message: str = "", *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message or self.error_code)
        self.message = message or self.error_code
        self.details = details or {}


class CheckpointTooLargeError(MatsoError):
    """壓縮後快照超過 MAX_CHECKPOINT_BYTES（ADR 002 Phase 2 應改物件儲存）。"""

    error_code = "CHECKPOINT_TOO_LARGE"


class RollbackTargetNotFoundError(MatsoError):
    """指定的 rollback 目標 tick 沒有對應的 checkpoint。"""

    error_code = "ROLLBACK_TARGET_NOT_FOUND"


class TerrainUnavailableError(MatsoError):
    """Terrain 插件不可達（gRPC 失敗或斷路器開啟）。物理預檢硬依賴——上層應 PAUSE session。"""

    error_code = "TERRAIN_UNAVAILABLE"
    http_status = 503  # 服務暫時不可用（terrain DOWN）


# ---------------- 認證（O4.1，SPEC §12） ----------------


class AuthInvalidCredentialsError(MatsoError):
    """登入帳號或密碼錯誤。訊息刻意不區分「帳號不存在」與「密碼錯」以防列舉。"""

    error_code = "AUTH_INVALID_CREDENTIALS"
    http_status = 401


class AuthInvalidTokenError(MatsoError):
    """token 簽章無效 / 格式錯誤 / 類型不符（如把 refresh 當 access 用）。"""

    error_code = "AUTH_INVALID_TOKEN"
    http_status = 401


class AuthTokenExpiredError(MatsoError):
    """token 已過期。"""

    error_code = "AUTH_TOKEN_EXPIRED"
    http_status = 401


class AuthForbiddenError(MatsoError):
    """已認證但角色/scope 無權執行此操作（SPEC §12 角色權限）。"""

    error_code = "AUTH_FORBIDDEN"
    http_status = 403


class ScenarioNotFoundError(MatsoError):
    error_code = "SCENARIO_NOT_FOUND"
    http_status = 404


# ---------------- Order pipeline（O3.1） ----------------


class SessionNotFoundError(MatsoError):
    error_code = "SESSION_NOT_FOUND"
    http_status = 404


class OrderNotFoundError(MatsoError):
    error_code = "ORDER_NOT_FOUND"
    http_status = 404


class OrderValidationError(MatsoError):
    """語法/單位存在性等驗證失敗（步驟 [1]）。error_code 由建構者指定（見 orders.validator）。"""

    error_code = "ORDER_INVALID"
    http_status = 422

    def __init__(
        self, message: str, *, error_code: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.error_code = error_code  # 實例層覆寫（不同驗證項不同 code）


class OrderPermissionError(MatsoError):
    """下令者無權對該單位下令（faction/角色）。"""

    error_code = "ORDER_PERMISSION_DENIED"
    http_status = 403


class PrecheckFailedError(MatsoError):
    """物理預檢不可行（步驟 [2]）——立即 REJECTED，回 422 + 具體 code + 各項結果。"""

    http_status = 422

    def __init__(
        self, message: str, *, error_code: str, details: dict[str, Any] | None = None
    ) -> None:
        super().__init__(message, details=details)
        self.error_code = error_code


class IllegalOrderTransitionError(MatsoError):
    """非法的 Order 狀態轉移（如取消已完成的指令）。"""

    error_code = "ORDER_INVALID_TRANSITION"
    http_status = 409


# ---------------- AI 子系統（O6.2，SPEC_FULL §9–10） ----------------


class AiDisabledError(MatsoError):
    """AI 於此 session 為 AI_OFF（傳統兵推模式）——AI 端點/功能不可用。"""

    error_code = "AI_DISABLED"
    http_status = 409


class GuardrailRejectedError(MatsoError):
    """AI 輸出被護欄硬性阻擋（G1 schema 重試耗盡 / G4 IHL-ROE）——回 fallback 或升 White Cell。"""

    error_code = "AI_OUTPUT_REJECTED"
    http_status = 422
