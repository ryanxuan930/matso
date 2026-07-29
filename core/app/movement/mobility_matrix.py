"""Core 端機動成本矩陣（讀 contracts/mobility_matrix.json）——#81 Phase B 地形調速用。

契約公式（權威＝`contracts/mobility_matrix.json` 的 $comment，與 modules/terrain 同源）：

    step_cost = profiles[profile][terrain_class] × (1 + slope_penalty[profile] × slope_deg / 45)

- `profiles[profile][terrain_class] == -1` → **不可通行**（回 None）。
- 未知 profile / terrain_class → 回 1.0（不調速；安全退化，不誤判不可通行）。

註：terrain 服務回的 `CellInfo.mobility_cost` 是 **profile 無關**（僅坡度）；per-profile 速度必須用
本矩陣（讀 terrain 的 `terrain_class` + `slope_deg` 再套此公式）。成本↑＝越難走＝越慢。

## 想定覆寫（WP-B6）

`MobilityRules` 是一份**已解析的矩陣值物件**；`DEFAULT_RULES` 是出貨預設。想定的
`overrides/mobility_matrix.json` 經 `MobilityRules.merged(patch)` 得到該局專屬的規則，
由 runner 於啟動時注入 `UnitMovementSystem`（與預覽端）。

**刻意不用「清 lru_cache 再重灌」**：同一個 core 行程同時跑 N 場推演局
（`sim_runtime._tasks`），任何全域可變狀態都會跨局污染，且結果取決於哪一局先啟動
——非決定性。這與 `sim_params` 模組 docstring 的第 3 條紀律一致（不做全域可變狀態，
以明確傳遞的值物件承載）。模組級的 `step_cost` / `road_speed_factor` 保留為
「以出貨預設代理」的薄殼，既有呼叫端與測試零改動。
"""

from __future__ import annotations

import functools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_MATRIX_PATH = Path(__file__).resolve().parents[3] / "contracts" / "mobility_matrix.json"
_SLOPE_REF_DEG = 45.0


@dataclass(frozen=True, slots=True)
class MobilityRules:
    """一份已解析的機動規則（出貨預設，或疊上想定覆寫後的該局規則）。"""

    profiles: dict[str, dict[str, float]]
    slope_penalty: dict[str, float]
    road_factors: dict[str, float]
    road_usable: dict[str, bool]

    def step_cost(self, profile: str, terrain_class: str, slope_deg: float) -> float | None:
        """(profile, terrain_class, slope) → 每格通行成本倍率。不可通行 → None。"""
        row = self.profiles.get(profile)
        if row is None:
            return 1.0
        base = row.get(terrain_class)
        if base is None:
            return 1.0
        if base < 0:
            return None  # 不可通行
        factor = self.slope_penalty.get(profile, 0.0)
        s = min(max(slope_deg, 0.0), _SLOPE_REF_DEG)
        return base * (1.0 + factor * s / _SLOPE_REF_DEG)

    def road_speed_factor(self, profile: str, road_class: str) -> float | None:
        """有道路時的速度係數（0<f≤1）。無路 / 該 profile 不能用路 → None。"""
        if not road_class:
            return None
        if not self.road_usable.get(profile, True):
            return None
        f = self.road_factors.get(road_class)
        return f if f is not None and f > 0 else None

    def passability(self) -> dict[tuple[str, str], bool]:
        """(profile, terrain_class) → 是否可通行。覆寫的合法性檢查用（見模組 docstring）。"""
        return {
            (profile, klass): value >= 0
            for profile, row in self.profiles.items()
            for klass, value in row.items()
        }

    def merged(self, patch: Any) -> MobilityRules:
        """疊上一份**局部**覆寫（只列要改的鍵）→ 新的規則物件。非 dict / 空 → 回自身。"""
        if not isinstance(patch, dict) or not patch:
            return self
        profiles = {p: dict(row) for p, row in self.profiles.items()}
        for profile, row in (patch.get("profiles") or {}).items():
            if isinstance(row, dict):
                profiles.setdefault(str(profile), {}).update(_floats(row))
        road = patch.get("road") or {}
        return MobilityRules(
            profiles=profiles,
            slope_penalty={**self.slope_penalty, **_floats(patch.get("slope_penalty") or {})},
            road_factors={**self.road_factors, **_floats(road.get("speed_factor_by_class") or {})},
            road_usable={
                **self.road_usable,
                **{str(k): bool(v) for k, v in (road.get("usable_by_profile") or {}).items()},
            },
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> MobilityRules:
        road = data.get("road") or {}
        return cls(
            profiles={
                str(p): _floats(row)
                for p, row in (data.get("profiles") or {}).items()
                if isinstance(row, dict)
            },
            slope_penalty=_floats(data.get("slope_penalty") or {}),
            road_factors=_floats(road.get("speed_factor_by_class") or {}),
            road_usable={str(k): bool(v) for k, v in (road.get("usable_by_profile") or {}).items()},
        )


def _floats(raw: dict[str, Any]) -> dict[str, float]:
    """只收真正的數值（過濾 `$comment` 等說明字串與 bool）。"""
    return {
        str(k): float(v)
        for k, v in raw.items()
        if isinstance(v, (int, float)) and not isinstance(v, bool)
    }


@functools.lru_cache(maxsize=1)
def default_rules() -> MobilityRules:
    """出貨預設（`contracts/mobility_matrix.json`）。不可變值物件，故快取安全。"""
    return MobilityRules.from_dict(json.loads(_MATRIX_PATH.read_text(encoding="utf-8")))


def road_speed_factor(profile: str, road_class: str) -> float | None:
    """出貨預設的道路速度係數（薄殼；該局有覆寫時請用注入的 `MobilityRules`）。

    回 None 表示「照原本越野+地形模型走」；有值則呼叫端改用道路速度且**不再套地形/坡度成本**
    （路面已鋪整，林中公路不該按森林算）。
    """
    return default_rules().road_speed_factor(profile, road_class)


def step_cost(profile: str, terrain_class: str, slope_deg: float) -> float | None:
    """出貨預設的每格通行成本（薄殼；該局有覆寫時請用注入的 `MobilityRules`）。

    不可通行（矩陣值 -1）→ None（呼叫端據此 MOVE_BLOCKED）。
    未知 profile / terrain_class → 1.0（不調速）。
    """
    return default_rules().step_cost(profile, terrain_class, slope_deg)
