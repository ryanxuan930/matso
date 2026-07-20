"""偵測模型（SPEC §7.2）——資料驅動 SensorProfile + 偵測機率 + 情報等級分級。

純同步純函數（同 adjudication；HOW_TO §3）：不碰 DB/Redis/時鐘/RPC。欄位對映
`contracts/weaponeering.schema.json` 的 `sensor` $def。

偵測機率 = 距離衰減（detect_curve）× LOS × 天氣 × 目標特徵 × 隱蔽姿態，夾在 [0,1]。
情報等級（DETECTED→CLASSIFIED→IDENTIFIED）由偵測機率高低（＝條件好壞）推導。
"""

from __future__ import annotations

from dataclasses import dataclass
from itertools import pairwise
from typing import Any

from app.models.enums import IntelFidelity

# 需要 LOS 的感測器類型（光學/紅外/雷達）；聲學/電子偵蒐不需直視
_NEEDS_LOS = frozenset({"OPTICAL", "IR", "RADAR"})

# 情報等級門檻（偵測機率愈高＝條件愈好＝等級愈高）
_IDENTIFY_THRESHOLD = 0.85
_CLASSIFY_THRESHOLD = 0.55

# 各等級的定位誤差半徑（公尺）——等級愈高定位愈準
ERROR_RADIUS_M = {
    IntelFidelity.DETECTED: 500.0,
    IntelFidelity.CLASSIFIED: 200.0,
    IntelFidelity.IDENTIFIED: 50.0,
}


@dataclass(frozen=True, slots=True)
class SensorProfile:
    sensor_kind: str  # OPTICAL / IR / RADAR / ACOUSTIC / SIGINT
    max_range_m: float
    detect_curve: tuple[tuple[float, float], ...]  # (range_max_m, p_detect) 控制點

    @classmethod
    def from_base_stats(cls, stats: dict[str, Any]) -> SensorProfile:
        curve_raw = stats.get("detect_curve")
        if not curve_raw:
            raise ValueError("sensor baseStats 缺 detect_curve")
        curve = tuple((float(r), float(p)) for r, p in curve_raw)
        if any(not 0.0 <= p <= 1.0 for _, p in curve):
            raise ValueError("p_detect 必須落在 [0,1]")
        if any(a[0] >= b[0] for a, b in pairwise(curve)):
            raise ValueError("detect_curve 的 range 必須嚴格遞增")
        return cls(
            sensor_kind=str(stats["sensor_kind"]),
            max_range_m=float(stats["max_range_m"]),
            detect_curve=curve,
        )

    @property
    def needs_los(self) -> bool:
        return self.sensor_kind in _NEEDS_LOS

    def base_detect(self, range_m: float) -> float:
        """距離衰減後的基礎偵測率（控制點線性插值，端點外夾住）。"""
        curve = self.detect_curve
        if range_m <= curve[0][0]:
            return curve[0][1]
        if range_m >= curve[-1][0]:
            return curve[-1][1]
        for (r0, p0), (r1, p1) in pairwise(curve):
            if r0 <= range_m <= r1:
                t = (range_m - r0) / (r1 - r0)
                return p0 + t * (p1 - p0)
        return curve[-1][1]  # pragma: no cover — 已被端點夾住


@dataclass(frozen=True, slots=True)
class DetectionEnv:
    """Kernel 事先收集的偵測環境係數（LOS + 乘法修正）。裁決函數不做任何 RPC。"""

    los_clear: bool
    weather_modifier: float = 1.0
    target_signature_modifier: float = 1.0  # 尺寸/熱訊號/移動中→放大；隱形→縮小
    concealment_modifier: float = 1.0  # 隱蔽姿態→縮小


def detect_probability(sensor: SensorProfile, range_m: float, env: DetectionEnv) -> float:
    """(sensor, target) 對的偵測機率。需 LOS 的感測器在無 LOS 時為 0。"""
    if range_m > sensor.max_range_m:
        return 0.0
    if sensor.needs_los and not env.los_clear:
        return 0.0
    p = (
        sensor.base_detect(range_m)
        * env.weather_modifier
        * env.target_signature_modifier
        * env.concealment_modifier
    )
    return min(1.0, max(0.0, p))


def fidelity_for(p_detect: float) -> IntelFidelity:
    """由偵測機率（條件好壞）決定情報等級。呼叫方須先確認偵測成功（roll < p_detect）。"""
    if p_detect >= _IDENTIFY_THRESHOLD:
        return IntelFidelity.IDENTIFIED
    if p_detect >= _CLASSIFY_THRESHOLD:
        return IntelFidelity.CLASSIFIED
    return IntelFidelity.DETECTED
