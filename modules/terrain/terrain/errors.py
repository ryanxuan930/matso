"""Terrain module 領域例外。

terrain 是可獨立部署的插件服務（O2.5 gRPC 化），不得 import core 的 app.*——自帶例外階層。
gRPC 層（O2.5）將把這些例外映射為 status code。
"""

from __future__ import annotations


class TerrainError(Exception):
    """Terrain module 領域例外基底。"""


class DtedFileNotFoundError(TerrainError):
    """DTED 檔案不存在或不可讀（常見原因：外接硬碟未掛載、MATSO_DTED_PATH 未設定）。"""


class OutOfBoundsError(TerrainError):
    """查詢座標落在 DTED 覆蓋範圍（bbox）之外——屬呼叫方錯誤，不靜默回海面。"""
