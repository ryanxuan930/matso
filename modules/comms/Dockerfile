# MATSO Comms 插件 gRPC 服務（O5.4）
# 建置 context = repo root（uv workspace 需各 member pyproject + contracts + ops/tools）
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /workspace

COPY . .

RUN uv sync --frozen --no-dev --package matso-comms
# gRPC stubs 不入 git（ADR 005）——build 內產生。grpcio-tools 僅 build 期臨時取得。
RUN uv run --no-dev --with grpcio-tools python ops/tools/gen_proto.py

EXPOSE 50053
# 存活探針：能取得 manifest 即代表 gRPC server 已起
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=15s \
  CMD ["/workspace/.venv/bin/python", "-c", "import grpc; from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc; plugin_base_pb2_grpc.PluginBaseServiceStub(grpc.insecure_channel('localhost:50053')).GetManifest(plugin_base_pb2.GetManifestRequest(), timeout=3)"]

CMD ["/workspace/.venv/bin/python", "-m", "comms", "--host", "0.0.0.0", "--port", "50053"]
