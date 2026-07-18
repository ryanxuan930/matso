# contracts/ — 介面契約（權威）

一切跨模組介面在此定義。**先改契約、跑契約測試、再寫實作**（HOW_TO.md §0.3）。

| 檔案 | 內容 | 消費者 |
|------|------|--------|
| `core_api.yaml` | Core REST API（OpenAPI 3.1） | platform/（型別生成）、schemathesis |
| `ws_protocol.md` | WebSocket envelope 與訊息型別 | core, platform |
| `proto/matso/plugin/v1/plugin_base.proto` | 所有插件必須實作的基礎 gRPC 服務 | modules/* |
| `proto/matso/terrain/v1/terrain.proto` | Terrain module 領域 RPC | core, modules/terrain |
| `weather_payload.schema.json` | 天氣模組標準化輸出 | core, modules/weather |
| `scenario.schema.json` | Scenario Package（scenario.yaml 等）驗證 | core, 想定編輯器 |
| `ai_output.schema.json` | 各 AI 角色的結構化輸出 schema | ai/, core guardrails |
| `weaponeering.schema.json` | EquipmentTemplate.baseStats 欄位規格 | core adjudication |
| `mobility_matrix.json` | mobility_profile × terrain_class 通行成本 | modules/terrain |

版本規則：semver 記於各檔頭；major 變更需同步 bump `plugin_base.proto` 的 `contract_version` 相容檢查（SPEC_FULL §16.3）。
Proto 以 `buf lint` + `buf breaking` 把關；JSON Schema 以 draft 2020-12 撰寫。
