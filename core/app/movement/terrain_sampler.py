"""移動地形取樣器建構（#81 Phase B）——執行器（sim_runtime）與預覽（api/movement）共用。

`build_terrain_cell_sampler()` 回一個 `h3_list → {h3:(terrain_class, slope_deg)}` 的取樣器；
STUB_GATEWAY / 無 grpc / 建立失敗 → None（不調速，退回 Phase A 直線 per-unit 速度）。
呼叫失敗於使用端以 try/except 退回不調速（terrain 服務中斷不凍結移動）。
"""

from __future__ import annotations

import logging
import os

from app.engine.movement import TerrainSampler

_LOG = logging.getLogger("app.movement.terrain_sampler")


def build_terrain_cell_sampler() -> TerrainSampler | None:
    if os.environ.get("STUB_GATEWAY"):
        return None
    try:
        import grpc

        from app.config import Settings
        from app.plugins import TerrainClient

        client = TerrainClient(grpc.insecure_channel(Settings().terrain_grpc_target))

        def _sample(cells: list[str]) -> dict[str, tuple[str, float]]:
            resp = client.get_cell_batch(list(cells))
            return {c.h3_index: (str(c.terrain_class), float(c.slope_deg)) for c in resp.cells}

        return _sample
    except Exception:
        _LOG.warning("移動地形取樣器建立失敗，速度不受地形調變")
        return None
