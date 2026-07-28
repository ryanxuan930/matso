---
task: "#59 自主推演一鍵啟動 + COP unit_scope 灰化"
status: DONE
started: 2026-07-24T12:10+08:00
updated: 2026-07-24T12:40+08:00
agent: Opus 4.8
---

# #59 AI 兵推怎麼啟動（自動重啟 runner）+ 下令面板灰化範圍外單位

## 目標摘要
1. 使用者問「AI 兵推要怎麼啟動」——已在自主主控台指派 AI 陣營並儲存，但 AI 沒動作。
2. （續前）把 unit_scope 套到 COP 下令面板：範圍外單位灰化/不可選。

## 根因（AI 啟動）
`sim_runtime._run_session` 於 **runner 起跑時讀一次** 自主指派（`start_ai_workers`）。session
一建立就自動起 runner（掃描迴圈），故使用者稍後在主控台存指派時 runner 已在跑、不含 AI worker；
且**無重啟入口**（主控台自己只警告「需重啟 runner 或新建 session」）。另一必要條件：系統設定的
AI 模式非 AI_OFF 且有 LLM base_url（此環境已設 AI_BARE + Google gemma-4-31b-it，故非此因）。

## 交付
- **AI 一鍵啟動**（559da41）：
  - `sim_control.session_restart_key`（runner 重啟旗標）。
  - `_run_session`：起跑先清旗標；`run_paced` should_stop 加輪詢此鍵 → 存在即結束本迴圈 → 掃描層
    `_ensure` 數秒內重建 → 重讀指派 → 起/停 AI worker。熱狀態於 Redis，戰局不中斷。
  - `api/autonomy` PUT/DELETE：存/清指派後設重啟旗標，回 `restarted=true`。
  - 前端主控台：儲存訊息 +「如何啟動」說明改寫（先設 AI 模式 + LLM 後端；按儲存即自動重啟）。
- **COP unit_scope 灰化**（f9e64f0）：
  - `SessionSummary +my_unit_scope`；lobby `_participant_scopes` 帶入呼叫者於本局 scope。
  - cop.vue `inScope(u)`（白軍/全知不限；空 scope＝整個陣營）；清單範圍外單位灰化 + 🚫 + 不可點選。

## 測試證據
- test_autonomy_api +3（PUT/DELETE 設旗標、非白軍 403）；orchestrator 6、lobby 10、participants 10 passed。
- mypy 193、ruff/openapi 綠、golden 6 綠、前端 lint/typecheck 綠；容器重建含新行為。

## 啟動 SOP（給使用者）
1. 系統設定：AI 模式 = AI_BARE/FULL + 填 LLM 後端（本環境已設）。
2. 自主推演主控台：勾 AI 陣營 + 任務目標 → 儲存指派 → **數秒內** AI 自動接管（不需新建 session）。
3. 回 COP 觀戰：AI 令入指令列。首次產令約一個心跳（45s）+ LLM 回應時間。

## 後續
- AI 首次決策較慢（心跳 + 雲端 LLM latency）；可加「AI 思考中/上次決策」狀態列。
- unit_scope 亦可套 ENGAGE 目標選擇。
