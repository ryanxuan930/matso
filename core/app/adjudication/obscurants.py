"""煙幕與遮蔽（WP-C4c）——純同步純函數（紅線 2）。

[JCATS-A p.19]：煙幕是化學兵的標準配屬，作用是阻視線。

## 煙幕是**雙面的**

規格明寫這一點，而它決定了整個介面的形狀：`blocks_los()` 不知道誰是誰，只判「這條
視線有沒有穿過活躍的煙」。放煙的一方同樣看不穿自己的煙——那正是煙幕在戰術上要付的代價
（掩護退卻的煙也擋住你自己的觀測）。任何帶 `faction` 參數的版本都會把這件事弄丟。

## 為什麼是 LOS 的布林覆寫而不是一個係數

煙不是「讓你看得比較模糊」，它是**遮蔽**。做成 0.3 之類的係數會讓「隔著煙幕狙擊」
變成一件機率低但可行的事，而那不是煙幕存在的理由。地形 LOS 已經是布林，煙疊在它後面
用同一個語義，兩者也就不會互相打架。

## 幾何重用既有那一份

判定就是「煙心到視線線段的最短距離 <= 半徑」，而 `movement/attrition` 早就有
`dist_point_to_segment_m`（含 cos-lat 修正）。**不另寫一份**——兩份幾何必然漂移，
WP-C2 的 `obstacles_at` 也是用同一個理由重用既有的線段判定。

## 消散：到期即消失，不做濃度衰減

`expires_at_tick` 是一刀切。真實的煙是逐漸稀薄的，但要模擬那個就得回到「係數」路線，
而那條路上面已經否決過。發煙者能控制的是**放多久**（發數 → 持續 tick），那才是決策點。
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from app.movement.attrition import dist_point_to_segment_m

# 一次發煙任務的預設半徑與持續 tick（1 tick = 1 分鐘）。
# v0 校準：一個中隊的發煙彈幕大約遮蔽 150 m 正面、撐 5–10 分鐘。
DEFAULT_SMOKE_RADIUS_M = 150.0
DEFAULT_SMOKE_TICKS = 8
# 每多一發延長的 tick 數——發數是發煙者唯一能調的旋鈕。
TICKS_PER_ROUND = 2


@dataclass(frozen=True, slots=True)
class SmokeCloud:
    """一團煙。座標為 (lat, lng)；`expires_at_tick` 之後即失效。"""

    lat: float
    lng: float
    radius_m: float
    expires_at_tick: int
    feature_id: str = ""

    def active_at(self, tick: int) -> bool:
        return tick < self.expires_at_tick and self.radius_m > 0.0


def duration_ticks(rounds: int) -> int:
    """發數 → 持續 tick。**發數是發煙者唯一能調的旋鈕**（見模組說明）。"""
    return DEFAULT_SMOKE_TICKS + TICKS_PER_ROUND * max(0, rounds - 1)


def blocks_los(
    a: tuple[float, float],
    b: tuple[float, float],
    clouds: Iterable[SmokeCloud],
    tick: int,
) -> bool:
    """`a`→`b`（皆為 (lat, lng)）的視線有沒有被活躍煙幕擋住。

    ⚠ **不看陣營**。煙是雙面的：放煙的人同樣看不穿自己的煙。
    """
    for cloud in clouds:
        if not cloud.active_at(tick):
            continue
        # `dist_point_to_segment_m` 收的是 (lng, lat) ——與 GeoJSON 同序。
        dist = dist_point_to_segment_m((cloud.lng, cloud.lat), (a[1], a[0]), (b[1], b[0]))
        if dist <= cloud.radius_m:
            return True
    return False


def active(clouds: Iterable[SmokeCloud], tick: int) -> list[SmokeCloud]:
    """本 tick 仍有效的煙。空 list ＝這一 tick 完全不必做幾何判定。"""
    return [c for c in clouds if c.active_at(tick)]


__all__ = [
    "DEFAULT_SMOKE_RADIUS_M",
    "DEFAULT_SMOKE_TICKS",
    "TICKS_PER_ROUND",
    "SmokeCloud",
    "active",
    "blocks_los",
    "duration_ticks",
]
