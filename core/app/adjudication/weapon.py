"""武器參數（資料驅動；SPEC §7.1）——由 EquipmentTemplate.baseStats 解析為 frozen 領域物件。

裁決引擎的公式讀這裡，**不寫死參數**（HOW_TO §4.2 步驟 1）。欄位對映
`contracts/weaponeering.schema.json` 的 `kinetic` $def。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any


@dataclass(frozen=True, slots=True)
class WeaponProfile:
    """KINETIC 武器包絡與命中/傷害表（純資料，無行為以外的狀態）。"""

    max_range_m: float
    min_range_m: float
    indirect_fire: bool
    # 由近到遠的 (range_max_m, base_ph) 控制點；區間內線性插值
    ph_by_range_band: tuple[tuple[float, float], ...]
    damage_by_armor_class: dict[str, float]
    ammo_types: tuple[str, ...]
    rate_per_tick: float

    @classmethod
    def from_base_stats(cls, stats: dict[str, Any]) -> WeaponProfile:
        """由 EquipmentTemplate.baseStats（kinetic）解析。結構性防呆用 ValueError。"""
        bands_raw = stats.get("ph_by_range_band")
        if not bands_raw:
            raise ValueError("kinetic baseStats 缺 ph_by_range_band")
        bands = tuple((float(r), float(p)) for r, p in bands_raw)
        if any(not 0.0 <= p <= 1.0 for _, p in bands):
            raise ValueError("base_ph 必須落在 [0,1]")
        # 控制點需依 range 遞增，供插值與單調性
        if any(a[0] >= b[0] for a, b in pairwise(bands)):
            raise ValueError("ph_by_range_band 的 range 必須嚴格遞增")
        return cls(
            max_range_m=float(stats["max_range_m"]),
            min_range_m=float(stats.get("min_range_m", 0.0)),
            indirect_fire=bool(stats.get("indirect_fire", False)),
            ph_by_range_band=bands,
            damage_by_armor_class={
                str(k): float(v) for k, v in stats["damage_by_armor_class"].items()
            },
            ammo_types=tuple(str(a) for a in stats["ammo_types"]),
            rate_per_tick=float(stats.get("rate_per_tick", 1.0)),
        )

    def base_ph(self, range_m: float) -> float:
        """射程 range_m 的基礎命中率（控制點間線性插值，端點外夾住）。

        控制點 base_ph 非遞增時，本函數對 range 亦非遞增（供單調性 property）。
        """
        bands = self.ph_by_range_band
        if range_m <= bands[0][0]:
            return bands[0][1]
        if range_m >= bands[-1][0]:
            return bands[-1][1]
        for (r0, p0), (r1, p1) in pairwise(bands):
            if r0 <= range_m <= r1:
                t = (range_m - r0) / (r1 - r0)
                return p0 + t * (p1 - p0)
        return bands[-1][1]  # pragma: no cover — 已被端點夾住，理論上不會到

    def in_envelope(self, range_m: float) -> bool:
        return self.min_range_m <= range_m <= self.max_range_m

    def damage_against(self, armor_class: str) -> float:
        """對該裝甲類別的傷害；未列入的裝甲類別視為無效（0）。"""
        return self.damage_by_armor_class.get(armor_class, 0.0)
