"""Core 端機動成本矩陣（讀 contracts/mobility_matrix.json）——#81 Phase B 地形調速用。

契約公式（權威＝`contracts/mobility_matrix.json` 的 $comment，與 modules/terrain 同源）：

    step_cost = profiles[profile][terrain_class] × (1 + slope_penalty[profile] × slope_deg / 45)

- `profiles[profile][terrain_class] == -1` → **不可通行**（回 None）。
- 未知 profile / terrain_class → 回 1.0（不調速；安全退化，不誤判不可通行）。

註：terrain 服務回的 `CellInfo.mobility_cost` 是 **profile 無關**（僅坡度）；per-profile 速度必須用
本矩陣（讀 terrain 的 `terrain_class` + `slope_deg` 再套此公式）。成本↑＝越難走＝越慢。
"""

from __future__ import annotations

import functools
import json
from pathlib import Path

_MATRIX_PATH = Path(__file__).resolve().parents[3] / "contracts" / "mobility_matrix.json"
_SLOPE_REF_DEG = 45.0


@functools.lru_cache(maxsize=1)
def _road() -> tuple[dict[str, float], dict[str, bool]]:
    """#83 道路區塊：road_class → 速度係數、profile → 是否可用路。"""
    data = json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))
    block = data.get("road") or {}
    factors = {
        str(k): float(v)
        for k, v in (block.get("speed_factor_by_class") or {}).items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    usable = {str(k): bool(v) for k, v in (block.get("usable_by_profile") or {}).items()}
    return factors, usable


def road_speed_factor(profile: str, road_class: str) -> float | None:
    """有道路時的速度係數（0<f≤1，乘上 road_kmh）。無路/該 profile 不能用路 → None。

    回 None 表示「照原本越野+地形模型走」；有值則呼叫端改用道路速度且**不再套地形/坡度成本**
    （路面已鋪整，林中公路不該按森林算）。
    """
    if not road_class:
        return None
    factors, usable = _road()
    if not usable.get(profile, True):
        return None
    f = factors.get(road_class)
    return f if f is not None and f > 0 else None


@functools.lru_cache(maxsize=1)
def _matrix() -> tuple[dict[str, dict[str, float]], dict[str, float]]:
    data = json.loads(_MATRIX_PATH.read_text(encoding="utf-8"))
    profiles = {
        str(p): {str(c): float(v) for c, v in row.items()} for p, row in data["profiles"].items()
    }
    slope = {
        str(p): float(v)
        for p, v in data["slope_penalty"].items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }
    return profiles, slope


def step_cost(profile: str, terrain_class: str, slope_deg: float) -> float | None:
    """(profile, terrain_class, slope) → 每格通行成本倍率（≥ base ≥ 1 通常）。

    不可通行（矩陣值 -1）→ None（呼叫端據此 MOVE_BLOCKED）。
    未知 profile / terrain_class → 1.0（不調速）。
    """
    profiles, slope_pen = _matrix()
    row = profiles.get(profile)
    if row is None:
        return 1.0
    base = row.get(terrain_class)
    if base is None:
        return 1.0
    if base < 0:
        return None  # 不可通行
    factor = slope_pen.get(profile, 0.0)
    s = min(max(slope_deg, 0.0), _SLOPE_REF_DEG)
    return base * (1.0 + factor * s / _SLOPE_REF_DEG)
