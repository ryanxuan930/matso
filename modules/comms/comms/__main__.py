"""Comms 插件服務進入點（O5.4）。

    uv run python -m comms                 # 預設埠 50053

純確定性服務（無外部資料來源 / 無設定）：起得來即 HEALTHY。地形遮蔽、天氣衰減、EW 干擾
皆由呼叫端（Core）於 ComputeLinks request 攜入。
"""

from __future__ import annotations

import argparse
import logging
import os

from matso_sdk import serve

from comms.plugin import CommsPlugin


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="MATSO Comms 插件 gRPC 服務")
    parser.add_argument("--port", type=int, default=int(os.environ.get("COMMS_GRPC_PORT", "50053")))
    parser.add_argument("--host", default="0.0.0.0")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    plugin = CommsPlugin()
    state, detail = plugin.health()
    logging.getLogger("comms").info("啟動，健康狀態：%s（%s）", state, detail)
    serve(plugin, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
