"""領域例外統一定義（HOW_TO §3.1）。

規則：領域錯誤一律拋此處定義的自訂例外；API 層（O3.1 起）統一轉為契約的 error code。
建構參數驗證（型別/範圍防呆）仍用內建 ValueError——那是程式設計錯誤，不是領域錯誤。
"""

from __future__ import annotations


class MatsoError(Exception):
    """所有 MATSO 領域例外的基底。error_code 對應 contracts/core_api.yaml 的 Error.code。"""

    error_code = "INTERNAL_ERROR"


class CheckpointTooLargeError(MatsoError):
    """壓縮後快照超過 MAX_CHECKPOINT_BYTES（ADR 002 Phase 2 應改物件儲存）。"""

    error_code = "CHECKPOINT_TOO_LARGE"


class RollbackTargetNotFoundError(MatsoError):
    """指定的 rollback 目標 tick 沒有對應的 checkpoint。"""

    error_code = "ROLLBACK_TARGET_NOT_FOUND"


class TerrainUnavailableError(MatsoError):
    """Terrain 插件不可達（gRPC 失敗或斷路器開啟）。物理預檢硬依賴——上層應 PAUSE session。"""

    error_code = "TERRAIN_UNAVAILABLE"
