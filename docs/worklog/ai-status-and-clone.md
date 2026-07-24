---
task: "#79 AI 決策狀態列（思考中/倒數）+ 複製推演為新局"
status: WIP
started: 2026-07-24T13:10+08:00
updated: 2026-07-24T13:10+08:00
agent: Opus 4.8
---

# #79 COP AI 決策狀態列 + 一鍵複製推演為新局

## 目標摘要
1. **AI 思考中／下一次決策倒數**：COP 觀戰時顯示各陣營 AI 決策心跳狀態
   （thinking＝正打 LLM；idle＝距下一次心跳倒數；offline＝worker 未上線/逾時）。
2. **複製推演為新局**：由現有一局的**目前 DB 狀態**（部署座標/戰力/編裝/地圖標註/參與者
   名冊/AI 指派）verbatim 複製出一局新推演，另給新 RNG 種子。使用者不需重設初始位置與編裝。

## 關鍵設計事實（開工前查證）
- **無「初始快照」**：`movement.py` / `adjudicator.py` 於執行期把 current_lat/lng、
  current_strength、health、equipment.current_state(ammo) **寫回 DB**。DB 即活權威，hot state
  只在鍵不存在時由 DB 播種（`seed_combat_state`）。故複製 DB＝複製「當下」狀態；**開打前複製
  ＝純淨初始局**（本功能的建議用法，已於 UI 提示）。
- AI worker 為獨立 async 任務、**牆鐘心跳**（非決定性 kernel）→ 狀態列時間戳用 `time.time()`
  屬營運遙測，不違反 SimClock 紅線（該紅線只約束決定性物理）。
- 遙測寫 Redis hash `session:{id}:ai_status`（field=faction）；single-writer 仍是各自 worker
  只寫自己 field，無競態。endpoint 讀 hash + server_now 算倒數 + faction 過濾（fog：一般角色
  僅見己方 AI 狀態）。

## 交付
- **契約**（core_api.yaml）：`POST /sessions/{id}/clone`（→201 SessionSummary）、
  `GET /sessions/{id}/ai-status`（→AiStatusView）、schemas CloneSessionRequest/AiStatusView/
  AiFactionStatus；`npm run gen:api` 綠。
- **複製推演**（backend）：
  - `lobby/schemas.CloneSessionRequest`；`LobbyService.clone_session`——verbatim 複製 session 參數
    + 單位（兩階段連 parent）+ 裝備 + 地圖標註 + 參與者名冊（跳過 `ai-*`；unit_scope 依 old→new 重寫）；
    確保複製者為新局統裁；新 `master_seed`。限 `_require_director`。
  - `api/lobby.clone_session` 路由——服務落地後另把 Redis `ai_config` 複製到新局（掃描層起跑自動接管 AI）。
- **AI 狀態**（backend）：
  - `worker.run_faction_worker(+status_sink, +now)`——每週期發 thinking→idle 遙測（含下一次心跳/落單）；
    失敗週期也回 idle（不卡「思考中」）。`now` 注入（測試決定性）。
  - `orchestrator.ai_status_key` + `_make_status_sink`（HSET hash field=faction，單寫者無競態）。
  - `api/autonomy.get_ai_status`——讀 hash + server 端算倒數 + 逾時判 offline；**faction 過濾**（fog：
    一般角色僅見己方）；DELETE autonomy 一併清 ai_status。
- **前端**：
  - lobby.vue：每局卡新增「複製」鈕（pi-copy）+ 命名 modal + `doClone`（建立後直接進新局 COP）。
  - cop.vue：AI 狀態列（思考中／下一次決策倒數／離線）；`useAiStatus`（8s 權威重抓 + 本地每秒遞減）。

## 測試證據
- 後端新增：clone 複製單位/裝備/parent 重連/新 seed + 非統裁 403；worker thinking→idle 遙測；
  ai-status 倒數 + faction 過濾 + delete 清狀態。全套 `uv run pytest` 1012 passed / 8 skipped（golden 綠）。
- 關卡：ruff / mypy(193) / schema-sync / buf lint / 前端 lint+typecheck 全綠。

## 設計備註（給使用者）
- 複製為「當下 DB 狀態」快照 → **開打前複製＝純淨初始局**（已於 modal 提示）；已交戰則沿用當下座標/戰力。
  無「初始快照」機制（sim 執行期把座標/戰力/彈藥寫回 DB），故此為最誠實且正確的作法。
- 新局給**新 RNG 種子**（新一輪獨立隨機），並沿用 AI 指派（任務/心跳）→ 一鍵再戰。

## 中斷續作指引
- 已完成。剩：重建 core 容器 + 對執行中容器實測 clone/ai-status（API）→ 前端瀏覽器驗收 → PROGRESS 更新。
