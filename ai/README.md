# ai/ — AI 指揮參謀子系統（SPEC_FULL §9–10, §19.4）

| 目錄 | 內容 |
|------|------|
| `inference/` | vLLM OpenAI-compatible client、RoleManager（LoRA 熱切換、OPFOR 佇列優先） |
| `prompts/` | 各角色 system prompt（.md + YAML frontmatter：version/adapter/output_schema）— prompt 是程式碼，走 PR |
| `rag/` | Qdrant 入庫管線（bge-m3 嵌入）、collections、引用查核 API（護欄 G5 用） |
| `training/` | RAFT 資料合成、CPT/SFT 腳本（D-CPT Law 比例為設定參數）— Phase 2 重點 |
| `evals/` | 內部 WARBENCH 風格評測（IHL 兩難／殘缺情報／假情報注入）+ runner |

鐵律：
1. 所有輸出走 `contracts/ai_output.schema.json` + Guardrail Gateway，無例外。
2. AI 產生的 order 一樣要過物理預檢——沒有繞過物理引擎的特權。
3. prompt / adapter 變更必須通過 eval gate（SPEC_FULL §19.4 門檻）才可上線。
4. 量化部署（≤8-bit）自動觸發 G6 加嚴（WARBENCH 教訓）。

五個 Phase 1 角色：STRATEGIC_PLANNER / OPFOR_COMMANDER / AAR_ANALYST / INTEL_OFFICER / WHITE_CELL_ASSISTANT（SPEC_FULL §9.1）。
