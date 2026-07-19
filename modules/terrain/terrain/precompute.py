"""Hex grid 離線預計算 CLI（O2.2）。

讀 DTED（需外接硬碟）→ 為指定 bbox × resolution 的所有 H3 cell 算屬性 → 寫 parquet 快取。
之後查詢只讀該快取（HexGridCache），不再需要外接硬碟。

用法：
    # 用 DTED 全覆蓋範圍、res 8、寫到設定的 hex_cache_dir
    MATSO_DTED_PATH=/Volumes/M200/Maps/TW_ALL.tif \\
        uv run python -m terrain.precompute

    # 指定 bbox 與輸出
    uv run python -m terrain.precompute --bbox 121.0 23.5 121.5 24.0 --res 8 \\
        --out /path/to/hexcache/res8.parquet
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from terrain.config import TerrainSettings
from terrain.dted import DtedMap
from terrain.hexgrid import HexGridBuilder, write_parquet


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="預計算 H3 hex grid → parquet 快取")
    parser.add_argument("--res", type=int, default=8, help="H3 resolution 7–9（預設 8）")
    parser.add_argument(
        "--bbox",
        type=float,
        nargs=4,
        metavar=("MIN_LNG", "MIN_LAT", "MAX_LNG", "MAX_LAT"),
        default=None,
        help="覆蓋範圍（預設用 DTED 全範圍）",
    )
    parser.add_argument(
        "--out", type=Path, default=None, help="輸出 parquet（預設 hex_cache_dir/res{N}.parquet）"
    )
    parser.add_argument("--dted", type=Path, default=None, help="DTED 路徑（預設 MATSO_DTED_PATH）")
    args = parser.parse_args(argv)

    settings = TerrainSettings()
    dted_path = args.dted if args.dted is not None else settings.dted_path
    out = args.out if args.out is not None else settings.hex_cache_dir / f"res{args.res}.parquet"

    t0 = time.perf_counter()
    with DtedMap.open(dted_path) as dted:
        bbox = tuple(args.bbox) if args.bbox else dted.bounds
        builder = HexGridBuilder(dted)
        count = write_parquet(builder.build_region(bbox, args.res), out)
    dt = time.perf_counter() - t0
    print(f"預計算完成：{count} cells（res{args.res}, bbox={bbox}）→ {out}（{dt:.1f}s）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
