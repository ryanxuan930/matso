"""PathPlanner 的真實轉接（O3.4）——包裝 terrain gRPC client 的 GetPath。

裝配時：`TerrainClientPlanner(app.plugins.TerrainClient(channel))`。不可達回空清單
（movement 視為立即結束）。以鴨子型別持有 client，避免 import 環。

## 現況：生產環境零呼叫端（2026-07-30 查證）

活執行期的移動由 `app/engine/movement.py` 的 `UnitMovementSystem`（連續經緯度版）負責，
`sim_runtime` 只組裝它；路徑規劃走 `app/movement/router.plan_route`
（地形 A*，經 `terrain_sampler` 取格點）。本檔屬 O3.4 的 **hex 逐格版**三件組
（`system.py` / `db_store.py` / 本檔），已被上述取代。

⚠ **`app/movement/` 這個套件本身不是死的**——盤點曾記成「整個平行子系統是死實作」，
查證後不成立：`params` / `mobility` / `mobility_matrix` / `session_mobility` /
`terrain_sampler` / `router` / `fuel` / `attrition` 全都有生產呼叫端（`sim_runtime`、
`api/movement`、`engine/*`、`orders/precheck`）。死的只有那三件組。

**留著不刪**：`system.py` / `db_store.py` 是 M3 里程碑驗收
（`tests/integration/test_scripted_battle.py`）的移動實作，本檔則由
`tests/unit/test_movement_planner.py` 單獨釘住「不可達回空清單」這條契約。
三件組要清就要一起清並處理那三支測試，單獨刪本檔只會留下一組更破碎的 legacy。
"""

from __future__ import annotations


class TerrainClientPlanner:
    def __init__(self, client: object) -> None:
        self._client = client  # app.plugins.TerrainClient

    def plan(self, from_h3: str, to_h3: str, mobility_profile: str) -> list[str]:
        resp = self._client.get_path(from_h3, to_h3, mobility_profile)  # type: ignore[attr-defined]
        return list(resp.h3_path) if resp.reachable else []
