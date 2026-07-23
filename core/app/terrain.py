"""地形對交戰的效果係數（真實化交戰 Phase 3 STEP3）——純同步純函數。

terrain 服務未提供地表分類（森林/城鎮）查詢；但視線查詢（check_los）回傳「最小餘隙
clearance_m」——射線掠過地形冠部（餘隙小）代表目標半遮蔽於稜線/地物後 → 較難命中（cover）。
本模組把餘隙映到「地形遮蔽命中修正 terrain_cover_modifier」，讓真實地形進入 p_hit（不再固定 1.0）。

紅線：不碰時鐘/RNG/DB/RPC；given clearance 具決定性 → replay 安全。cover(命中)與 concealment
(偵測)分離：此處只管命中。間瞄（indirect）彈道越過地形，不受地形遮蔽 → 由呼叫端傳 1.0。
"""

from __future__ import annotations

import math

# 餘隙 ≥ 此值＝開闊地，目標全暴露（cover 1.0）。
_FULL_EXPOSURE_M = 25.0
# 掠地/剛好通視（餘隙 ≤ 0）時的最小命中係數（強地形遮蔽）。
_MIN_COVER = 0.55


def engagement_cover_modifier(clearance_m: float | None) -> float:
    """由視線最小餘隙導出地形遮蔽命中修正（0.55–1.0）。

    - clearance 無效/None（無地形資料、間瞄）→ 1.0（不修正）。
    - clearance ≥ 25m（開闊）→ 1.0。
    - clearance ≤ 0（掠地/半遮蔽）→ 0.55。
    - 之間線性內插——餘隙越小、地形遮蔽越強、命中越低。
    """
    if clearance_m is None or not math.isfinite(clearance_m):
        return 1.0
    if clearance_m >= _FULL_EXPOSURE_M:
        return 1.0
    if clearance_m <= 0.0:
        return _MIN_COVER
    return _MIN_COVER + (1.0 - _MIN_COVER) * (clearance_m / _FULL_EXPOSURE_M)
