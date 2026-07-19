"""合成 DTED 夾具產生器（O2.1）。

產生一個小型、**完全確定性**的 GeoTIFF（WGS84），供沒有真檔（TW_ALL.tif 在外接硬碟）
時開發與 CI 測試。**刻意對齊真檔 TW_ALL.tif 的慣例：`nodata=0.0`、float32、EPSG:4326**。
內容為封閉解析式，任何點的期望高程可在測試中獨立重算：

- 覆蓋範圍：lng [121.0, 121.5]、lat [23.5, 24.0]（台灣東部山區附近的 0.5°×0.5° 方框）
- 陸地：基準 BASE_M + 以 (121.25, 23.75) 為中心的餘弦山峰（確保陸地 != 0.0，不與 nodata 混淆）
- 海域：lng > 121.4 的東側帶狀區 → 0.0（== nodata → water=True，模擬真檔）
- 低於海平面陸地：西緣 lng < 121.05 帶 → -3.0m（測試「負高程不得被當成海」，SPEC §4.1 min -3.01m）

也可當 CLI 用：uv run python modules/terrain/tests/make_fixture.py /tmp/fixture.tif
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import numpy as np
import rasterio
from rasterio.transform import from_bounds

# 夾具幾何常數（測試據此重算期望值——改動即需同步測試）
WEST, SOUTH, EAST, NORTH = 121.0, 23.5, 121.5, 24.0
WIDTH, HEIGHT = 200, 200  # 每像素 0.0025°（~275m）
PEAK_LNG, PEAK_LAT = 121.25, 23.75
PEAK_M = 3000.0
BASE_M = 50.0  # 陸地基準高，確保 land != 0.0（0.0 = nodata，對齊真檔慣例）
FALLOFF_DEG = 0.5
SEA_LNG_EAST_OF = 121.4  # 此經度以東為海域（0.0 = nodata）
BELOW_SEA_LNG_WEST_OF = 121.05  # 此經度以西為低於海平面陸地帶
BELOW_SEA_M = -3.0
NODATA = 0.0  # 對齊真檔 TW_ALL.tif（實測 ds.nodata == 0.0）


def expected_elevation(lat: float, lng: float) -> float | None:
    """夾具的封閉解析式（None = 海域 nodata）。測試用它獨立驗證 DtedMap 的讀值。"""
    if lng > SEA_LNG_EAST_OF:
        return None  # 海域 → 寫入 0.0 nodata
    if lng < BELOW_SEA_LNG_WEST_OF:
        return BELOW_SEA_M  # 低於海平面陸地（負值，非 nodata）
    d = math.hypot(lng - PEAK_LNG, lat - PEAK_LAT)
    return BASE_M + PEAK_M * max(0.0, math.cos(math.pi * d / FALLOFF_DEG))


def write_fixture(path: Path) -> Path:
    transform = from_bounds(WEST, SOUTH, EAST, NORTH, WIDTH, HEIGHT)
    data = np.empty((HEIGHT, WIDTH), dtype=np.float32)
    for row in range(HEIGHT):
        for col in range(WIDTH):
            # 像素中心座標（與 rasterio index/xy 的取樣慣例一致）
            lng, lat = rasterio.transform.xy(transform, row, col)
            value = expected_elevation(lat, lng)
            data[row, col] = NODATA if value is None else value
    # 注意：NODATA=0.0，land 皆 !=0（BASE_M/BELOW_SEA_M），故無混淆

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=WIDTH,
        height=HEIGHT,
        count=1,
        dtype="float32",
        crs="EPSG:4326",
        transform=transform,
        nodata=NODATA,
    ) as dst:
        dst.write(data, 1)
    return path


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("/tmp/matso_dted_fixture.tiff")
    print(f"fixture written: {write_fixture(out)}")
