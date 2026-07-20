"""Terrain 插件服務進入點（O2.5）。

    MATSO_DTED_PATH=/Volumes/M200/Maps/TW_ALL.tif \\
        uv run python -m terrain [--port 50051]

外接硬碟未掛載時仍會啟動（health=DEGRADED/DOWN），由 Core 端健檢決定預案。
"""

from __future__ import annotations

import argparse
import logging
import os

from matso_sdk import serve

from terrain.plugin import build_from_settings


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MATSO Terrain 插件 gRPC 服務")
    parser.add_argument(
        "--port", type=int, default=int(os.environ.get("TERRAIN_GRPC_PORT", "50051"))
    )
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--res", type=int, default=8, help="viewshed / 快取 resolution")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    plugin = build_from_settings(resolution=args.res)
    state, detail = plugin.health()
    logging.getLogger("terrain").info("啟動健康狀態：%s（%s）", state, detail)
    serve(plugin, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
