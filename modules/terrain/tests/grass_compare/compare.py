"""GRASS r.viewshed 對照（O2.3 驗收骨架；方法見同目錄 README）。

我方 get_viewshed vs GRASS r.viewshed 在 100 個確定性抽樣觀測點的可見性一致率 ≥ 98%。
GRASS docker 呼叫（_grass_visibility）為 release 前必完成的 TODO。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import h3
from terrain.dted import DtedMap
from terrain.los import Observer, get_viewshed

N_OBSERVERS = 100
RADIUS_M = 3000.0
OBSERVER_AGL_M = 10.0
RESOLUTION = 8
AGREEMENT_THRESHOLD = 0.98


@dataclass(frozen=True, slots=True)
class CompareResult:
    total: int
    agreements: int

    @property
    def ratio(self) -> float:
        return self.agreements / self.total if self.total else 0.0


def sample_observers(dted: DtedMap, n: int = N_OBSERVERS) -> list[Observer]:
    """DTED 範圍內確定性均勻抽樣 n 個陸地觀測點（非亂數，可重現）。"""
    west, south, east, north = dted.bounds
    observers: list[Observer] = []
    i = 0
    while len(observers) < n and i < n * 20:
        lat = south + (north - south) * ((i * 37) % n) / n
        lng = west + (east - west) * ((i * 61) % n) / n
        i += 1
        if not dted.get_elevation(lat, lng).water:  # 陸地才取
            observers.append(Observer(lat=lat, lng=lng, height_agl_m=OBSERVER_AGL_M))
    return observers


def grass_available() -> bool:
    return shutil.which("docker") is not None and _grass_image_present()


def _grass_image_present() -> bool:
    try:
        out = subprocess.run(
            ["docker", "images", "-q", "osgeo/grass-gis"],
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return bool(out.stdout.strip())


def _grass_visibility(dted_path: Path, observer: Observer, targets: list[str]) -> dict[str, bool]:
    """對 observer 跑 GRASS r.viewshed，回傳每個 target h3 cell 中心是否可見。

    TODO（release 前）：docker run osgeo/grass-gis → r.in.gdal 匯入 DTED → g.region →
    r.viewshed coordinates=lng,lat observer_elevation=AGL max_distance=RADIUS -c →
    r.what 於各 target cell 中心取樣可見性 raster。
    """
    raise NotImplementedError("GRASS docker 呼叫待實作（O2.3 backlog，release 前必完成）")


def compare(dted_path: Path) -> CompareResult:
    total = 0
    agreements = 0
    with DtedMap.open(dted_path) as dted:
        for observer in sample_observers(dted):
            ours = set(get_viewshed(dted, observer, RADIUS_M, RESOLUTION))
            # 對照集合：observer 周邊 grid_disk 內的 cell（我方可見 vs GRASS 可見逐一比對）
            targets = list(
                h3.grid_disk(h3.latlng_to_cell(observer.lat, observer.lng, RESOLUTION), 8)
            )
            grass = _grass_visibility(dted_path, observer, targets)
            for cell in targets:
                if cell not in grass:
                    continue
                total += 1
                if (cell in ours) == grass[cell]:
                    agreements += 1
    return CompareResult(total=total, agreements=agreements)


def main() -> int:
    from terrain.config import TerrainSettings

    path = TerrainSettings().dted_path
    result = compare(path)
    print(f"GRASS 對照：{result.agreements}/{result.total} = {result.ratio:.3%}")
    return 0 if result.ratio >= AGREEMENT_THRESHOLD else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
