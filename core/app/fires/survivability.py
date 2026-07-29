"""陣地變換（WP-C10.5）——砲兵打久了要換位置，否則會被反砲兵火力找到。

**本模組的幾何與設定解析是純函數**（紅線 2）；真正把砲移過去的是一道 MOVE 令，
不是直接改座標。理由值得寫清楚，因為「直接寫座標」看起來省事得多：

1. **會被復原**：`seed_combat_state` 每次 runner 啟動都**無條件**以 DB 座標覆蓋熱狀態的
   lat/lng（只有這兩個欄位沒有「缺鍵才補」的保護）。只寫熱狀態的位移，重啟就跳回去。
2. **會瞬移**：一個 STATE_DIFF 直接跳 1.5 km，沒有地形、沒有油耗、沒有行軍耗損，
   而且敵人看得到一門砲憑空移動。
3. **會撞車**：裁決在 tick 的最前面、移動在後面，同一個 tick 內單位若還有 MOVE 令，
   移動子系統會從 DB 重讀座標把它走回去。
4. **會繞過唯一的可達性閘門**：`path_reachable` 只長在 `OrderService.submit` 那條路上。

下 MOVE 令則以上四件事全部免費解決，代價是多一次 gateway 呼叫與令列上多一筆——
後者其實是好事：自動位移要看得見才追究得了。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from app.engine.rng import DeterministicRNG

_EARTH_R_M = 6_371_000.0

# 未宣告時的預設。**只在 enabled=True 時才有意義**——整包缺席就是停用。
_DEFAULT_MISSIONS = 3
_DEFAULT_MIN_KM = 1.0
_DEFAULT_MAX_KM = 2.0

# 熱狀態上的計數鍵。**不是 DB 欄位**：回滾會還原熱狀態但不還原 TacticalUnit 的欄位，
# 計數放 DB 的話回滾後會帶著未來的次數活下來——那比「崩潰時掉一個 checkpoint 間隔」更糟。
MISSION_COUNT_KEY = "missions_since_displacement"

# 能自走的機動 profile。牽引砲要牽引車，而 repo 裡**沒有 TOWED_GUN 範本、
# 也沒有任何程式讀 `logistics.transport.can_tow`**——為它寫的分支會是沒有測試資料的虛構。
SELF_MOVING_PROFILES = frozenset({"TRACKED", "WHEELED"})


@dataclass(frozen=True, slots=True)
class SurvivabilityConfig:
    """想定宣告的陣地變換設定。`enabled=False` ＝ 排程器整個不動作。"""

    enabled: bool = False
    missions_before_move: int = _DEFAULT_MISSIONS
    min_km: float = _DEFAULT_MIN_KM
    max_km: float = _DEFAULT_MAX_KM


def parse_survivability_config(raw: dict[str, Any] | None) -> SurvivabilityConfig:
    """想定 JSON → 設定。缺席／`enabled` 非真 → 停用（既有局零行為變更）。

    `min_km > max_km` 時對調而不是報錯：想定作者填反了不該讓整局起不來，
    而且對調後的意圖是明確的。
    """
    if not isinstance(raw, dict) or not raw.get("enabled"):
        return SurvivabilityConfig()
    missions = raw.get("missions_before_move", _DEFAULT_MISSIONS)
    lo = float(raw.get("min_km", _DEFAULT_MIN_KM))
    hi = float(raw.get("max_km", _DEFAULT_MAX_KM))
    if lo > hi:
        lo, hi = hi, lo
    return SurvivabilityConfig(
        enabled=True,
        missions_before_move=max(1, int(missions) if isinstance(missions, (int, float)) else 1),
        min_km=max(0.0, lo),
        max_km=max(0.0, hi),
    )


def pick_displacement_point(
    lat: float, lng: float, rng: DeterministicRNG, cfg: SurvivabilityConfig
) -> tuple[float, float]:
    """從當前陣地抽一個新陣地：隨機方位、距離落在 [min_km, max_km]。

    **兩次抽樣，順序固定**（方位、距離）——抽樣次數與設定無關，否則調一個參數就會
    擾動整條 stream 的後續序列。

    這裡不判地形：可達性由 `OrderService.submit` 的預檢決定（那是唯一的閘門）。
    抽到不可達的方位就換一個抽，呼叫端負責重試。
    """
    bearing = rng.random() * 2.0 * math.pi
    dist_m = rng.uniform(cfg.min_km, cfg.max_km) * 1000.0
    north = dist_m * math.cos(bearing)
    east = dist_m * math.sin(bearing)
    dlat = north / _EARTH_R_M * (180.0 / math.pi)
    dlng = east / (_EARTH_R_M * math.cos(math.radians(lat))) * (180.0 / math.pi)
    return lat + dlat, lng + dlng


def load_session_survivability(db: Any, session_id: str) -> SurvivabilityConfig:
    """讀本局的陣地變換設定（開局快照，非即時讀想定）。未宣告 → 停用。"""
    from app.models.tables import WargameSession

    session = db.get(WargameSession, session_id)
    raw = getattr(session, "survivability_move", None) if session is not None else None
    return parse_survivability_config(raw if isinstance(raw, dict) else None)


__all__ = [
    "MISSION_COUNT_KEY",
    "SELF_MOVING_PROFILES",
    "SurvivabilityConfig",
    "load_session_survivability",
    "parse_survivability_config",
    "pick_displacement_point",
]
