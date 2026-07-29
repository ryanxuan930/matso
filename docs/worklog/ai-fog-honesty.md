---
task: "WP-A1 AI 敵情接上真實情報（迷霧誠實化）"
status: DONE
started: 2026-07-29T09:20+08:00
updated: 2026-07-29T10:40+08:00
agent: Opus 5
spec: SPEC_V2.md §6 WP-A1；相關 SPEC_FULL §7.2（偵測）、§9（AI）、§13.3（fog 渲染）
---

# WP-A1 AI 敵情接上真實情報

## 目標摘要
`ai_loop` 的 AI 陣營指揮官原本用 `ground_truth_enemies`——**全知敵方存活單位位置**，迷霧對 AI 不成立。
SensorSweepSystem（#97）與 IntelService 都已上線，本卡把 AI 敵情換成該陣營真實的 `IntelContact`
投影（與 `GET /intel` 同語意），補上盟軍共享視圖與 `recent_events`，並留 `ai_ground_truth` 退回開關。

## 交付
| 檔案 | 動作 | 說明 |
|------|------|------|
| `core/app/ai_loop/world_view.py` | **新增** | 該陣營可見世界的三個查詢：`contacts_from_intel`（敵情，複用 IntelService 投影）、`allied_units`（盟軍共享視圖）、`recent_events`（Ledger 受眾過濾） |
| `core/app/ai_loop/context.py` | 修改 | 新增 `allied_units` 桶與渲染；`_fmt_enemy` 補**最後觀測 tick 與誤差半徑**；新增 `_fmt_event`（原本 dict 直接 str 化成 Python repr） |
| `core/app/ai_loop/worker.py` | 修改 | `run_decision_cycle` 每週期取盟軍與近期事件並餵進 context |
| `core/app/ai_loop/orchestrator.py` | 修改 | **接線**：`enemy_visibility` 預設改 `contacts_from_intel`；讀 `ai_ground_truth` 開關（true → 回 ground truth 並記 warning） |
| `core/app/api/autonomy.py` | 修改 | `AutonomyConfig` 加 `ai_ground_truth: bool = False`（未宣告的欄位 pydantic 會丟掉，白軍就設不了） |
| `core/tests/unit/test_ai_fog_honesty.py` | **新增** | 8 條：未偵測不出現、fidelity 分級、contact 是最後已知位置、盟軍可見、敵對不入盟軍桶、事件受眾過濾、SENSOR_CONTACT 排除 |
| `core/tests/unit/test_ai_orchestrator.py` | 修改 | 2 條接線測試（預設走真實偵測／開關切回全知） |

## 三個設計決定（規格沒寫、我裁決的）

### 1. 新模組而非塞進 `context.py`
SPEC_V2 步驟 1 指定落點是 `context.py`，但該檔 docstring 明訂**零 I/O 純投影**。
把 DB 查詢塞進去會破壞那條不變式，故另立 `world_view.py`。**規格與程式衝突時以程式的既有紀律為準**。

### 2. 敵情條目帶真實 `unit_id`，且 prompt 就以它為識別碼（★最大的坑）
`ContactView` 刻意不含 `target_unit_id`（去識別化紅線），但 `orders_bridge` 與物理預檢**都以真實
`TacticalUnit.id` 查目標**。若敵情只給 `contact_id`，LLM 產出的 ENGAGE 令一律橋接失敗
→ **AI 接上迷霧後就再也打不了任何目標，只會 MOVE**——那會被誤判成「迷霧生效」，實則是功能壞掉。

考慮過「LLM 用 contact_id、伺服端事後反查」，最後不採：反查得插在 decider 輸出與 G3 護欄之間
（G3 的 precheck 也要真 id），會多一層可繞過的轉換。而**去識別化的實質保護不因此減損**——
contact 對同一目標是 upsert，`contact_id` 本身就是穩定識別碼，兩者的跨時關聯能力相同；
真正的敵情內容（番號/型號/陣營）仍由 fidelity 閘門控制。
殘留問題「穩定 id 可跨時關聯身分」需輪替識別碼才能解，已記 PROGRESS backlog。

### 3. 事件 feed 排除 `SENSOR_CONTACT`
其受眾雖然只有觀測方，但 `target_id` 是**被偵測單位的真實 id**（`intel/schemas.py` 明訂永不下發）。
敵情已由 known_enemies 呈現，放行只會讓去識別化在事件欄位上破功。連同 `UNIT_MOVED`/`TICK_OVERRUN`
（雜訊）一併排除。

## 測試證據
- 新增 8 + 2 條測試；**全套 `uv run pytest -m "not benchmark"` → 1116 passed / 8 skipped**。
- **golden 6 未破**（AI 是牆鐘心跳的 async worker、不進 Kernel tick；golden 用 NoOp 子系統。
  掃描已逐項查證 `core/tests/replay/` 完全不 import `app.ai_loop`）。
- ruff / ruff format / mypy(206) / schema_sync(141 欄) 全綠。

### 容器實跑（複製一局，驗完刪除；使用者的局全程未動）
複製 `玉山行動-0801` → 跑 45 秒讓感測器產出 70 筆真實 contacts → 唯讀比對：

| 陣營 | AI 敵情 | GET /intel | ground_truth | DETECTED 洩漏 |
|---|---|---|---|---|
| BLUE | 23 | 23 | 23 | 0 |
| RED | **22** | 22 | **23** | 0 |
| YELLOW | **25** | 25 | **26** | 0 |

**關鍵證據**：RED 22 < 23、YELLOW 25 < 26——AI 現在**嚴格看得比真相少**（BLUE 恰好全偵測到，
因為此想定各單位緊鄰）。且 AI 敵情條目數與 `GET /intel` **逐局相等**（同一投影，非兩份真相）。

實際送進 LLM 的態勢行長這樣（含新增的時間戳與誤差）：
```
## 已知敵情（23；僅列偵測所及，未偵測者不在此）
- ecb454af-… @ (24.2504,120.8452)｜RED R1 FIRETEAM｜IDENTIFIED｜最後觀測 tick 140（誤差 ±50m）
```

## 陷阱與已知行為（勿「順手修好」）
- **contact 是最後已知位置**：目標移走或已被殲滅仍留在敵情裡（IntelContact 無存活狀態、不過期），
  AI 因此會打空點——這是迷霧的本義。已用 `test_contact_persists_after_target_moves_or_dies` 釘住，
  防止日後有人用 ground truth 過濾死人而把迷霧漏回來。
- **DETECTED 級連目標陣營都不知道**：AI 在 prompt 層無從判斷該不該打；伺服端 ROE 仍以真實 faction
  裁定。這是規格要的誠實迷霧，不是 bug。
- 盟軍在 sweep 階段就不成為 contact（#91 共享視圖語義），故必須另走 `allied_units`——
  改版前盟軍對 AI 是**完全隱形**的（既不在 own_units 也不在 known_enemies）。

## 範圍外發現（已記 PROGRESS backlog，未順手修）
- **刪除 session 不清 `IntelContact`**：`delete_session` 手動維護的子表清單漏了它（該表無 FK/cascade）。
  全庫現有 **448 筆孤兒 contacts** 分屬 7 個已刪除的局。本次驗收自己產生的 70 筆已手動清除。
- 穩定識別碼可跨時關聯身分（見設計決定 2）。

## 中斷續作指引
- 本卡完成並驗收。後續：SPEC_V2 的 WP-A3（G4 no-strike 修復）與 WP-A2（任務級下令）。
- `recent_events` 在實跑中為 0 筆（複製局的帳本是新的、且多為被排除的 UNIT_MOVED）——
  受眾過濾的正確性由單元測試涵蓋；長局的實跑觀察值得日後補記。
