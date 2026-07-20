# ADR 005：gRPC codegen 用 grpcio-tools（離線），產物不入 git

- 狀態：Accepted
- 日期：2026-07-20
- 關聯：O2.5（插件化）、SPEC_FULL §16.3/§17/§18（air-gapped）、TASKS.md O2.5

## 背景

O2.5 需把 proto 契約（`contracts/proto/matso/**/v1/*.proto`）產成 Python gRPC stubs，供
`matso_sdk`（PluginBaseService）、terrain 插件（TerrainService server）、core（client）使用。
TASKS.md O2.5 字面建議「buf generate」。

## 決策

**用 `grpc_tools.protoc`（grpcio-tools）離線產生**，包成 `ops/tools/gen_proto.py`；
產物落在 `modules/_sdk/matso_sdk/_generated/`，**不入 git**（`.gitignore` + ruff/mypy 排除）。

理由：
1. **air-gapped（SPEC §18 硬需求）**：`buf generate` 的 remote plugins 需連 buf.build；local
   plugins 需另裝 `protoc-gen-python`/`protoc-gen-grpc-python` 二進位。`grpcio-tools` 是純
   Python wheel，已在 uv workspace，離線可跑、CI/Docker 無額外系統依賴。
2. **契約 lint 仍用 buf**：`buf lint`/`buf breaking` 維持（CI contracts job）——buf 負責契約
   品質，codegen 交給 grpcio-tools，職責分離。
3. **產物不入 git**：避免二進位 diff 噪音與手改風險；改契約後 `gen_proto.py` 重產。
   消費端匯入路徑穩定：`from matso_sdk._generated import <name>_pb2[_grpc]`。

## 實作要點

- `gen_proto.py` 以每個 proto 所在目錄為 `-I` 根 → 扁平檔名；再把 `*_pb2_grpc.py` 內
  `import x_pb2` 改寫為 `from . import x_pb2`（套件相對匯入）。
- `grpcio-tools` 為 **dev 依賴**（僅 codegen 期需要）；執行期只需 `grpcio`+`protobuf`。
- **哪裡要先跑 codegen**：CI python job（`uv sync` 後、lint/type/test 前）、terrain Dockerfile
  build 期（`uv run --with grpcio-tools`）、開發者首次 checkout 後。

## 後果

- 乾淨 checkout 後首次跑測試前必須先 `uv run python ops/tools/gen_proto.py`（否則
  `matso_sdk._generated` 不存在）。已在 CI 與 Dockerfile 自動化；本地開發需記得（見 HOW_TO）。
- 若未來要改回 buf generate（例如導入 buf 的 managed mode + 本地 protoc plugins），介面
  （`matso_sdk._generated.*`）不變，消費端不受影響。
