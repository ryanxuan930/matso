"""偵測掃描（SPEC §7.2）——H3 k-ring 空間預過濾把配對數從 O(N²) 降到近線性。

純同步、確定性（rng 注入）。對每個感測器：以其 max_range 換算 k，`grid_disk(cell, k)` 取
鄰近格內的候選目標（**空間索引預過濾**），再對存活配對做精確距離 + 偵測機率 + 擲骰。

k 取 `ceil(max_range / edge) + 1`（edge < 格心距 → 過度覆蓋，保證不漏任一射程內目標）。
迭代順序固定（感測器、候選皆按 unit_id 排序）→ rng 擲骰序列確定 → 與暴力全配對等價。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass

import h3

from app.engine.rng import DeterministicRNG
from app.intel.sensor import (
    ERROR_RADIUS_M,
    DetectionEnv,
    SensorProfile,
    detect_probability,
    fidelity_for,
)
from app.models.enums import IntelFidelity

_EARTH_R_M = 6_371_000.0


@dataclass(frozen=True, slots=True)
class SensorUnit:
    unit_id: str
    faction: str
    lat: float
    lng: float
    sensor: SensorProfile


@dataclass(frozen=True, slots=True)
class TargetUnit:
    unit_id: str
    faction: str
    lat: float
    lng: float


@dataclass(frozen=True, slots=True)
class Contact:
    """一次偵測結果（尚未落庫）。observer_faction 看到 target_unit_id。"""

    observer_faction: str
    target_unit_id: str
    fidelity: IntelFidelity
    tick: int
    lat: float
    lng: float
    error_radius_m: float


EnvLookup = Callable[[SensorUnit, TargetUnit], DetectionEnv]


def sweep(
    observers: Iterable[SensorUnit],
    candidates: Iterable[TargetUnit],
    env_for: EnvLookup,
    rng: DeterministicRNG,
    tick: int,
    resolution: int = 8,
) -> list[Contact]:
    """對所有感測器跑偵測掃描，回傳確定性排序的 Contact 清單。"""
    cand_list = sorted(candidates, key=lambda c: c.unit_id)
    index = _build_cell_index(cand_list, resolution)
    edge_m = float(h3.average_hexagon_edge_length(resolution, unit="m"))

    contacts: list[Contact] = []
    for observer in sorted(observers, key=lambda o: o.unit_id):
        near = _candidates_near(observer, index, edge_m, resolution)
        for target, range_m in _pairs_in_range(observer, near):
            env = env_for(observer, target)
            p = detect_probability(observer.sensor, range_m, env)
            roll = rng.random()  # 每個射程內敵對配對擲一次（順序固定 → 確定性）
            if roll < p:
                fidelity = fidelity_for(p)
                contacts.append(
                    Contact(
                        observer_faction=observer.faction,
                        target_unit_id=target.unit_id,
                        fidelity=fidelity,
                        tick=tick,
                        lat=target.lat,
                        lng=target.lng,
                        error_radius_m=ERROR_RADIUS_M[fidelity],
                    )
                )
    return contacts


def _build_cell_index(candidates: list[TargetUnit], resolution: int) -> dict[str, list[TargetUnit]]:
    index: dict[str, list[TargetUnit]] = {}
    for c in candidates:
        cell = h3.latlng_to_cell(c.lat, c.lng, resolution)
        index.setdefault(cell, []).append(c)
    return index


def _candidates_near(
    observer: SensorUnit,
    index: dict[str, list[TargetUnit]],
    edge_m: float,
    resolution: int,
) -> list[TargetUnit]:
    """grid_disk(observer_cell, k) 內的候選目標；k 過度覆蓋 max_range 保證不漏。"""
    observer_cell = h3.latlng_to_cell(observer.lat, observer.lng, resolution)
    k = math.ceil(observer.sensor.max_range_m / edge_m) + 1
    near: list[TargetUnit] = []
    for cell in h3.grid_disk(observer_cell, k):
        near.extend(index.get(cell, []))
    return sorted(near, key=lambda c: c.unit_id)


def _pairs_in_range(
    observer: SensorUnit, candidates: list[TargetUnit]
) -> Iterator[tuple[TargetUnit, float]]:
    """敵對且在射程內的 (target, range_m)，按 unit_id 排序（與暴力全配對同序）。"""
    for target in candidates:
        if target.faction == observer.faction:
            continue  # 己方不列為 contact
        range_m = _haversine_m(observer.lat, observer.lng, target.lat, target.lng)
        if range_m <= observer.sensor.max_range_m:
            yield target, range_m


def _haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dlmb / 2) ** 2
    return 2 * _EARTH_R_M * math.asin(math.sqrt(a))
