"""戰力→效能 映射（真實化交戰，Phase 1）——純同步純函數（HOW_TO §3、§4.2）。

單位當前戰力比 ratio = current_strength / authorized_strength 經「凹形折點曲線」映到作戰效能%
（供 COP 血條顯示與 health_state 判定）。凹形＝前段損失影響小、逼近崩潰點時效能陡降，符合
軍事慣例（部隊在約 30% 損失時失去戰鬥力，SPEC §7.1 / TO&E 慣例）。

**紅線**：不碰時鐘/RNG/DB/RPC；插值邏輯與 `weapon.base_ph` 一致（線性控制點、端點夾住）。
"""

from __future__ import annotations

from itertools import pairwise

# 預設效能曲線：[strength_ratio, effectiveness]（比例遞增、效能遞增）。
# 0.30 以下＝失去戰鬥力（效能 0）；滿編＝1.0。前段平緩、逼近折點陡降（凹形）。
DEFAULT_EFFECTIVENESS_CURVE: tuple[tuple[float, float], ...] = (
    (0.30, 0.0),
    (0.50, 0.40),
    (0.70, 0.70),
    (0.90, 0.95),
    (1.00, 1.00),
)

# 戰鬥力折點：戰力比低於此→戰鬥不能（DOWN）。
DEFAULT_BREAKPOINT_RATIO = 0.30


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else x


def interp_effectiveness(
    ratio: float, curve: tuple[tuple[float, float], ...] = DEFAULT_EFFECTIVENESS_CURVE
) -> float:
    """戰力比 → 作戰效能（0–1）。控制點間線性插值，端點外夾住，結果夾於 [0,1]。"""
    r = _clamp01(ratio)
    if r <= curve[0][0]:
        return curve[0][1]
    if r >= curve[-1][0]:
        return curve[-1][1]
    for (r0, e0), (r1, e1) in pairwise(curve):
        if r0 <= r <= r1:
            t = (r - r0) / (r1 - r0)
            return _clamp01(e0 + t * (e1 - e0))
    return curve[-1][1]  # pragma: no cover — 已被端點夾住


def effectiveness_pct(
    ratio: float, curve: tuple[tuple[float, float], ...] = DEFAULT_EFFECTIVENESS_CURVE
) -> float:
    """效能百分比（0–100），供 healthStatus 顯示欄位。"""
    return round(interp_effectiveness(ratio, curve) * 100.0, 2)


def health_state(ratio: float, breakpoint_ratio: float = DEFAULT_BREAKPOINT_RATIO) -> str:
    """戰力比 → 戰備狀態：OK（≥0.90）/ DEGRADED / DOWN（<折點，戰鬥不能）。"""
    if ratio < breakpoint_ratio:
        return "DOWN"
    if ratio >= 0.90:
        return "OK"
    return "DEGRADED"
