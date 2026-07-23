"""移動參數（單一真相）——預覽端（API）與執行端（sim_runtime）共用，確保估計與實跑一致。"""

from __future__ import annotations

# sim time：1 分 / tick（與 sim_runtime._TICK_RATE_MS 對齊）。
MOVE_TICK_RATE_MS: int = 60_000
# 單位地面移動速度（公里/時）。
MOVE_SPEED_KMH: float = 40.0
