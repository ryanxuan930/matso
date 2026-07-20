"""A* 路徑規劃（hex grid；SPEC §4.3 GetPath，p99<100ms）。

輸入 hex 快取（HexGridCache，**只讀 parquet、不需外接硬碟**）+ mobility_profile → 最佳路徑。

設計要點：
- **成本**：進入每個 cell 的成本由 `MobilityMatrix.step_cost`（契約公式）決定；`-1` 不可通行。
  起點自身不計費，`total_cost` = 沿路各「進入 cell」成本之和。
- **heuristic**：`haversine(cur, goal) / 最大單步距離 × 最小每格成本`——低估剩餘跳數 × 低估單格
  成本 ⇒ admissible ⇒ A* 回傳最佳路徑（property test「成本 ≤ 任一替代」據此成立）。
- **確定性（紅線 1）**：heap 以單調插入序破 f 值同分；鄰接以 h3 index 排序後展開 ⇒ 同輸入
  永遠回同一條路徑（golden replay 需要）。
- **h3 陷阱（HOW_TO §8）**：不用 `h3.grid_distance`（跨 base cell 遠距可能拋例外）——鄰接一律
  `grid_disk(cell,1)`，距離估計改用中心點 haversine。
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass

import h3

from terrain.hexgrid import CellAttributes, HexGridCache
from terrain.los import _haversine_m
from terrain.mobility import MobilityMatrix

# 單一 hex 相鄰中心距離上界（供 heuristic 低估跳數）：√3 × 平均邊長 × 安全係數。
# 係數 >1 讓「最大單步距離」偏大 ⇒ 跳數估計偏小 ⇒ heuristic 不高估 ⇒ 維持 admissible。
_STEP_MARGIN = 1.10


@dataclass(frozen=True, slots=True)
class PathResult:
    """對應 contracts terrain.proto GetPathResponse。"""

    h3_path: list[str]  # 含起訖；unreachable 時為空
    total_cost: float  # 沿路「進入 cell」成本之和
    eta_ticks: int  # 佔位：≈ ceil(total_cost)；真正速度換算於 O3.4 移動執行
    reachable: bool


def _unreachable() -> PathResult:
    return PathResult(h3_path=[], total_cost=0.0, eta_ticks=0, reachable=False)


def get_path(
    cache: HexGridCache,
    from_h3: str,
    to_h3: str,
    mobility_profile: str,
    matrix: MobilityMatrix | None = None,
) -> PathResult:
    """A* 最短機動路徑。不可達（含起訖不可通行/不在快取）回 reachable=False。

    from_h3/to_h3 解析度須相同（同一 hex grid）；profile 未知 ⇒ ValueError（呼叫方 bug）。
    """
    matrix = matrix or MobilityMatrix.default()
    if not matrix.has_profile(mobility_profile):
        raise ValueError(
            f"未知 mobility_profile={mobility_profile!r}；可用：{sorted(matrix.profiles)}"
        )
    if h3.get_resolution(from_h3) != h3.get_resolution(to_h3):
        raise ValueError("from_h3 與 to_h3 解析度不同，無法在同一 hex grid 規劃路徑")

    start = cache.get_cell(from_h3)
    goal = cache.get_cell(to_h3)
    if start is None or goal is None:
        return _unreachable()  # 起訖不在預計算範圍 ⇒ 未知地形，不規劃

    def cost_of(cell: CellAttributes) -> float | None:
        return matrix.step_cost(mobility_profile, str(cell.terrain_class), cell.slope_deg)

    # 起訖本身須可通行（站不上去/離不開即不可達）
    if cost_of(start) is None or cost_of(goal) is None:
        return _unreachable()
    if from_h3 == to_h3:
        return PathResult(h3_path=[from_h3], total_cost=0.0, eta_ticks=0, reachable=True)

    # heuristic 參數
    resolution = h3.get_resolution(from_h3)
    edge_m = float(h3.average_hexagon_edge_length(resolution, unit="m"))
    max_step_m = edge_m * math.sqrt(3.0) * _STEP_MARGIN
    min_cost = matrix.min_step_cost(mobility_profile)

    def heuristic(lat: float, lng: float) -> float:
        return (_haversine_m(lat, lng, goal.center_lat, goal.center_lng) / max_step_m) * min_cost

    # A*：g=已知最小成本，counter=插入序（確定性破同分）
    counter = 0
    open_heap: list[tuple[float, float, int, str]] = [
        (heuristic(start.center_lat, start.center_lng), 0.0, counter, from_h3)
    ]
    best_g: dict[str, float] = {from_h3: 0.0}
    came_from: dict[str, str] = {}
    closed: set[str] = set()

    while open_heap:
        _, g_cur, _, current = heapq.heappop(open_heap)
        if current == to_h3:
            return _reconstruct(came_from, to_h3, from_h3, g_cur)
        if current in closed:
            continue  # 過期堆項（已用更小 g 擴展過）
        closed.add(current)

        for neighbor in sorted(set(h3.grid_disk(current, 1)) - {current}):
            if neighbor in closed:
                continue
            cell = cache.get_cell(neighbor)
            if cell is None:
                continue  # 區域外 ⇒ 視為不可通行
            step = cost_of(cell)
            if step is None:
                continue  # 不可通行地形
            tentative = g_cur + step
            if neighbor not in best_g or tentative < best_g[neighbor]:
                best_g[neighbor] = tentative
                came_from[neighbor] = current
                counter += 1
                heapq.heappush(
                    open_heap,
                    (
                        tentative + heuristic(cell.center_lat, cell.center_lng),
                        tentative,
                        counter,
                        neighbor,
                    ),
                )
    return _unreachable()


def _reconstruct(came_from: dict[str, str], goal: str, start: str, total_cost: float) -> PathResult:
    path = [goal]
    node = goal
    while node != start:
        node = came_from[node]
        path.append(node)
    path.reverse()
    return PathResult(
        h3_path=path,
        total_cost=total_cost,
        eta_ticks=math.ceil(total_cost),  # 佔位：速度換算於 O3.4
        reachable=True,
    )
