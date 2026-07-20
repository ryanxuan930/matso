# MATSO Terrain 插件 gRPC 服務（O2.5）
# 建置 context = repo root（uv workspace 需各 member 的 pyproject + contracts + ops/tools）
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /workspace

# rasterio/GDAL wheel 執行期需要的系統庫（bookworm-slim 已精簡掉）：
# libexpat1（XML 解析，GDAL 依賴）。缺它 import rasterio 會 ImportError libexpat.so.1。
RUN apt-get update \
  && apt-get install -y --no-install-recommends libexpat1 \
  && rm -rf /var/lib/apt/lists/*

COPY . .

# 安裝 terrain（含 matso-sdk workspace 依賴，editable）
RUN uv sync --frozen --no-dev --package matso-terrain
# 產生 gRPC stubs（不入 git）——grpcio-tools 僅 build 期臨時取得，不進執行期環境
RUN uv run --no-dev --with grpcio-tools python ops/tools/gen_proto.py

EXPOSE 50051
# 存活探針：能取得 manifest 即代表 gRPC server 已起（DTED/快取缺席時服務仍在，狀態為 DEGRADED/DOWN）
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=15s \
  CMD ["/workspace/.venv/bin/python", "-c", "import grpc; from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc; plugin_base_pb2_grpc.PluginBaseServiceStub(grpc.insecure_channel('localhost:50051')).GetManifest(plugin_base_pb2.GetManifestRequest(), timeout=3)"]

CMD ["/workspace/.venv/bin/python", "-m", "terrain", "--host", "0.0.0.0", "--port", "50051"]
