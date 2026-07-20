# MATSO Core Orchestrator（M0-3）
# 建置 context = repo root（uv workspace 需要各 member 的 pyproject）
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
WORKDIR /workspace

COPY . .
RUN uv sync --frozen --no-dev --package matso-core
# gRPC stubs 不入 git（ADR 005）——core 於 import 時鏈到 matso_sdk._generated
# （app.api.deps → app.plugins.TerrainClient），故 build 內產生。grpcio-tools 僅 build 期臨時取得。
RUN uv run --no-dev --with grpcio-tools python ops/tools/gen_proto.py

EXPOSE 8000
HEALTHCHECK --interval=10s --timeout=3s --retries=5 --start-period=15s \
  CMD ["/workspace/.venv/bin/python", "-c", "import urllib.request; urllib.request.urlopen('http://localhost:8000/healthz', timeout=2)"]

CMD ["/workspace/.venv/bin/uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--app-dir", "core"]
