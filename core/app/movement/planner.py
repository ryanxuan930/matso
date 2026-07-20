"""PathPlanner 的真實轉接（O3.4）——包裝 terrain gRPC client 的 GetPath。

裝配時：`TerrainClientPlanner(app.plugins.TerrainClient(channel))`。不可達回空清單
（movement 視為立即結束）。以鴨子型別持有 client，避免 import 環。
"""

from __future__ import annotations


class TerrainClientPlanner:
    def __init__(self, client: object) -> None:
        self._client = client  # app.plugins.TerrainClient

    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]:
        resp = self._client.get_path(from_h3, to_h3, mobility_profile)  # type: ignore[attr-defined]
        return list(resp.h3_path) if resp.reachable else []
