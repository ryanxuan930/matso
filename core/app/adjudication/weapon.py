"""武器參數（資料驅動；SPEC §7.1）——由 EquipmentTemplate.baseStats 解析為 frozen 領域物件。

裁決引擎的公式讀這裡，**不寫死參數**（HOW_TO §4.2 步驟 1）。欄位對映
`contracts/weaponeering.schema.json` 的 `kinetic` $def。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from itertools import pairwise
from typing import Any

# 命中率對射程的插值法（baseStats.ph_interp）。linear＝控制點間線性；polynomial＝
# 拉格朗日多項式穿過全部控制點（曲線平滑，#4）。未知值退回 linear。
_PH_INTERP_MODES = ("linear", "polynomial")


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def _lagrange_eval(points: tuple[tuple[float, float], ...], x: float) -> float:
    """拉格朗日插值：回傳穿過 `points` 的多項式在 x 的值。points 的 x 需相異（已保證嚴格遞增）。"""
    total = 0.0
    for i, (xi, yi) in enumerate(points):
        term = yi
        for j, (xj, _) in enumerate(points):
            if i != j:
                term *= (x - xj) / (xi - xj)
        total += term
    return total


@dataclass(frozen=True, slots=True)
class WeaponProfile:
    """KINETIC 武器包絡與命中/傷害表（純資料，無行為以外的狀態）。"""

    max_range_m: float
    min_range_m: float
    indirect_fire: bool
    # 由近到遠的 (range_max_m, base_ph) 控制點；區間內依 ph_interp 插值
    ph_by_range_band: tuple[tuple[float, float], ...]
    damage_by_armor_class: dict[str, float]
    ammo_types: tuple[str, ...]
    rate_per_tick: float
    # 命中率插值法："linear"（預設）或 "polynomial"（拉格朗日多項式，#4）
    ph_interp: str = "linear"
    # 動能武器細分（現代軍事分類，N10）：SMALL_ARMS/AUTOCANNON/ATGM/TANK_MAIN_GUN/…（供 UI 與
    # 未來子型行為；引擎現以 pk/ph 資料驅動，kinetic_kind 為分類元資料）。
    kinetic_kind: str = "GENERIC"
    # 每發對各裝甲級別的擊殺機率 P(kill|hit) ∈ [0,1]（真實化交戰 Phase 1）。有值＝命中造成
    # 期望傷亡＝pk×每平台戰力；無值則退回 damage_by_armor_class[ac]/100 以相容既有種子。
    pk_by_armor_class: dict[str, float] = field(default_factory=dict)
    # 飛彈（導引）類：missile=True 才套用飛彈接戰可行性規則（#飛彈）。
    # maneuverable=True（巡弋/遊蕩/ATGM…）→ 末端機動繞過，僅判射程；
    # maneuverable=False（彈道飛彈/無導引火箭）→ 走固定拋物線，需射程 + 拋物線淨空（地形/障礙）。
    missile: bool = False
    maneuverable: bool = True
    apex_ratio: float = 0.25  # 拋物線頂高比（45° 發射≈0.25；低伸彈道用較小值）

    @property
    def ballistic(self) -> bool:
        """不可變軌飛彈（走拋物線，接戰須判地形/障礙淨空）。"""
        return self.missile and not self.maneuverable

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
        interp = str(stats.get("ph_interp", "linear"))
        if interp not in _PH_INTERP_MODES:
            interp = "linear"
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
            ph_interp=interp,
            pk_by_armor_class={
                str(k): float(v) for k, v in (stats.get("pk_by_armor_class") or {}).items()
            },
            kinetic_kind=str(stats.get("kinetic_kind", "GENERIC")),
            # 飛彈類：以 missile_kind 存在判定為飛彈；maneuverable 預設 True（巡弋式，僅判射程），
            # 彈道飛彈於 baseStats 設 maneuverable=false → ballistic（走拋物線）。
            missile="missile_kind" in stats,
            maneuverable=bool(stats.get("maneuverable", True)),
            apex_ratio=float(stats.get("apex_ratio", 0.25)),
        )

    def base_ph(self, range_m: float) -> float:
        """射程 range_m 的基礎命中率（控制點間插值，端點外夾住，結果夾於 [0,1]）。

        - linear（預設）：相鄰控制點線性插值；base_ph 非遞增時對 range 亦非遞增（單調性 property）。
        - polynomial（#4）：拉格朗日多項式穿過全部控制點，曲線更平滑；≥3 點才有意義（2 點＝直線，
          等同 linear）。多項式不保證單調，故結果夾於 [0,1]。
        """
        bands = self.ph_by_range_band
        if range_m <= bands[0][0]:
            return bands[0][1]
        if range_m >= bands[-1][0]:
            return bands[-1][1]
        if self.ph_interp == "polynomial" and len(bands) >= 3:
            return _clamp01(_lagrange_eval(bands, range_m))
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

    def expected_casualties(self, armor_class: str) -> float:
        """命中時對該裝甲級別的期望傷亡（每平台擊殺機率，真實化交戰 Phase 1）。

        優先用 pk_by_armor_class（P(kill|hit)∈[0,1]）；無則退回 damage_by_armor_class[ac]/100
        （把舊的 0–100 傷害值視為百分比擊殺率），確保既有種子在導入 pk 前行為相容。
        """
        if self.pk_by_armor_class:
            return self.pk_by_armor_class.get(armor_class, 0.0)
        return self.damage_by_armor_class.get(armor_class, 0.0) / 100.0
