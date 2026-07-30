---
task: V2.1 WP-F3
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-F3 RoleManager 與 AIInvocationLog 接入活執行期

## 這是同一個病的第三例

`RoleManager` 與 `InvocationLogWriter` 寫好了、測試也有，但在 repo 裡的**非測試引用是 0**
——`LlmFactionDecider` 直連 `client.complete()`。前兩例：

1. **WP-B2 MSEL**：`sim_runtime` 傳 `NoOpTriggerChecker`，MSEL 引擎從未被呼叫。
2. **WP-A2 任務令**：`mission_planner` 根本沒傳，吃到 `NoOpMissionPlanner`。

後果：活自主推演**一筆 AI 稽核紀錄都沒有**。[INDSR p.57] 的回放歸因在 AI 側的對應物
（「AI 當時為什麼這樣下令」可考）整個缺席，而那是 F5 評量與 G6 白軍確認流的資料基礎。

## 最關鍵的一件事：路由不可以改掉 prompt

`RoleManager` 預設用**註冊表的靜態** system prompt，而 decider 用的是
`build_system_prompt(role, mode)` 組出來的**模式感知**版本。直接路由過去 prompt 就變了
——而 `ReplayClient` 是**按 prompt 雜湊重播**的，**所有已錄的自主場次會在那一刻全部失效**。

故 `AIRequest` 加一個 `system_prompt` 覆寫欄位（`None` ＝用註冊表的，既有行為不變），
decider 把自己組的 prompt 帶過去。有一條測試直接比對「直連」與「路由」兩條路徑的
messages **逐字相同**。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| ai/matso_ai/inference/role_manager.py | 修改 | `AIRequest.system_prompt` 覆寫（None＝註冊表版本） |
| core/app/ai_loop/decider.py | 修改 | `role_manager` 注入 + `bind_session()` + `_make_role_manager()`（建不起來回 None 但**留 log**） |
| core/app/ai_loop/orchestrator.py | 修改 | 傳 `session_id`（稽核要對得回是哪一局） |
| core/app/api/system.py | 修改 | `ai_loop_wired: False → True`（自 O11 起就過時） |
| core/tests/unit/test_ai_audit_wiring.py | 新增 | 8 條，全部打在**組裝點** |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1897 passed, 8 skipped, 4 deselected**
- `core/tests/replay` → **8 passed（golden 未重錄）**
- ruff / mypy(263) / 前端兩閘門 → clean
- 突變測試 4 個全數被抓：路由時用註冊表 prompt、decider 不走 RoleManager、
  factory 預設關掉稽核、session_id 不綁進稽核

## 決策與陷阱

**測試打在組裝點，不在 RoleManager 自己的行為上**（那已經有測試）。這個缺陷的形狀就是
「元件對、沒人接」，所以要驗的是接線。同 `test_live_kernel_wiring.py` 的理由。

**`_make_role_manager` 建不起來回 None 而不是拋**：稽核掛掉不該讓整個自主推演停擺，
但**一定要留 log**——否則「為什麼沒有稽核紀錄」會變成一個無跡可循的問題。

**`audit=True` 是預設**，並有一條測試盯著那個預設值——擋的是「下一個人把它關掉」，
那會讓稽核靜靜消失而沒有任何測試轉紅。

**`ai_loop_wired` 從 O11 起就是過時的 False**。自主迴路那時就已經接進 `sim_runtime`
（per-faction worker），旗標一直沒跟著改。順手修正並更新那條斷言的註解。

## 中斷續作指引

- **下一步第一件事**：F1（SPEC_INGEST 最小切片）。
- **未竟項**：
  1. **佇列批次未生效**：decider 走的是 `invoke()`（單發），不是 `enqueue()` + `process_pending()`。
     批次/OPFOR 優先級要生效得讓 orchestrator 在一個心跳內收集多陣營請求再一次處理
     ——那會改變決策的時序語義（目前每個陣營是獨立 async worker），屬另一張卡。
  2. **`guardrail_result` 仍是 `{"status": "not_evaluated"}`**：護欄結果要落帳需要把
     `run_faction_turn` 的 findings 回傳到 RoleManager 這一層，兩者目前在不同呼叫階段。
  3. AAR 不顯示 AI 稽核紀錄（資料進了 `AIInvocationLog`，但沒有讀取端）。
