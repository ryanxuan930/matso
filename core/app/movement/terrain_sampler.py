"""移動地形存取建構（#81 Phase B / #82 Phase C）——執行器（sim_runtime）與預覽（api/movement）共用。

- `build_terrain_cell_sampler()`：`h3_list → {h3:(terrain_class, slope_deg)}`（地形調速）。
- `build_terrain_path_fn()`：`(from_h3,to_h3,profile) → (h3_path, reachable)`（A* 繞路）。

STUB_GATEWAY / 無 grpc / 建立失敗 → None（退回上一階段行為：不調速 / 直線），呼叫端另以
try/except 容錯——terrain 服務中斷**不凍結移動**（同交戰 LOS 中斷紀律）。
"""

from __future__ import annotations

import logging
import os
from typing import Any

from app.engine.movement import TerrainSampler
from app.movement.router import PathFn

_LOG = logging.getLogger("app.movement.terrain_sampler")


def _client() -> Any:
    """建 TerrainClient；STUB/無 grpc/失敗 → None。"""
    if os.environ.get("STUB_GATEWAY"):
        return None
    import grpc

    from app.config import Settings
    from app.plugins import TerrainClient

    return TerrainClient(grpc.insecure_channel(Settings().terrain_grpc_target))


def build_terrain_cell_sampler() -> TerrainSampler | None:
    try:
        client = _client()
        if client is None:
            return None

        def _sample(cells: list[str]) -> dict[str, tuple[str, float]]:
            resp = client.get_cell_batch(list(cells))
            return {c.h3_index: (str(c.terrain_class), float(c.slope_deg)) for c in resp.cells}

        return _sample
    except Exception:
        _LOG.warning("移動地形取樣器建立失敗，速度不受地形調變")
        return None


def build_terrain_path_fn() -> PathFn | None:
    """#82：A* 地形路徑查詢器（繞開不可通行）。None → 執行/預覽維持直線。"""
    try:
        client = _client()
        if client is None:
            return None

        def _path(from_h3: str, to_h3: str, profile: str) -> tuple[list[str], bool]:
            resp = client.get_path(from_h3, to_h3, profile)
            return list(resp.h3_path), bool(resp.reachable)

        return _path
    except Exception:
        _LOG.warning("移動地形路徑查詢器建立失敗，移動退回直線")
        return None
