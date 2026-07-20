"""MatsoPlugin base class（SPEC §17）。

寫一個新插件只需：子類化 `MatsoPlugin`、宣告 `manifest`、在 `register_domain_services`
把領域 servicer 掛上 gRPC server；健康/設定有預設樣板，可覆寫。基礎服務
（GetManifest/HealthCheck/Configure）由 SDK 統一提供（見 server.serve / harness）。
"""

from __future__ import annotations

from abc import ABC, abstractmethod

import grpc

from matso_sdk.health import HealthState
from matso_sdk.manifest import Manifest


class MatsoPlugin(ABC):
    """所有 MATSO 插件的基底。領域邏輯以 gRPC servicer 形式由子類提供。"""

    @property
    @abstractmethod
    def manifest(self) -> Manifest:
        """插件身分（name/kind/contract_version/capabilities）。"""

    @abstractmethod
    def register_domain_services(self, server: grpc.Server) -> None:
        """把領域 servicer（如 TerrainService）註冊到 gRPC server。

        由 SDK 在啟動時呼叫；PluginBaseService 已由 SDK 自動掛上，子類勿重複註冊。
        """

    def health(self) -> tuple[HealthState, str]:
        """回報健康狀態 + 說明。預設 HEALTHY；子類依資源可用性覆寫（DEGRADED/DOWN）。"""
        return HealthState.HEALTHY, ""

    def configure(self, config_json: str) -> tuple[bool, str]:
        """熱更新設定。預設不支援（回 False）；子類覆寫並自驗 schema。"""
        return False, "configure not supported by this plugin"
