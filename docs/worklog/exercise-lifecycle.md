---
task: WP-B1          # SPEC_V2 §6 WP-B1（演習專案實體與生命週期）
status: IN_PROGRESS
started: 2026-07-31T16:10+08:00
updated: 2026-07-31T16:10+08:00
agent: Opus 5
---

# WP-B1 演習專案（Exercise）與生命週期

## 目標摘要

一場演習遠大於一局模擬（[JCATS-A p.9–16] 的 17 步 SOP：整備會議、想定發佈、飽和測試、
預推、正式實施、每日檢討、撤收建檔）。MATSO 現在 **session 即全部**——沒有任何容器
把「兩次預推 + 一次正式局 + 一次檢討」裝在一起，也沒有階段推進的留痕。

本卡建 `Exercise` 父實體與其階段機，並讓 session 可掛在演習底下。
**掛不掛都行**：`exerciseId` 為 NULL ＝ 獨立局，行為與現在一個位元都不差。

## 開工前的偵察（5 個平行 agent，findings 見下）

動手前先把五個面掃過一遍。**掃出來的東西改變了本卡的形狀**，逐條記在這裡，
因為其中幾條是「照規格寫下去會出事」的：

### 規格與現況對不上的地方

1. **`status` 不是狀態，是導出字串**。`lobby/service.py:320` 由 `archived_at`/`end_time`
   在序列化當下算出 ACTIVE/ENDED/ARCHIVED。**沒有 status 欄、沒有 enum、沒有轉移驗證**，
   所以本卡的階段機**沒有任何既有骨架可重用**——是從零長出來的，不能假裝在擴充什麼。
2. **`end_time` 是死碼**。全 repo 只有三處：欄位宣告、`_summary` 的讀取、一句註解。
   **沒有任何地方寫入它**，所以 `status == "ENDED"` 至今不可達。REVIEW/ARCHIVED 階段
   不能假設有「結束一局」的既有路徑可掛——那條路也要自己鋪。
3. **沒有 session start**。`SimManager._session_ids()` 每 3 秒掃一次，把
   **每個 `archivedAt IS NULL` 的 session 都跑起來**——建列即開跑。
   所以 B4 的「簽證不符則拒起」不是某個 start 端點上的守衛，而是掃描過濾器
   或 `_ensure` 的早退（那裡已有 `session_concluded_key` 的同款形狀）。
   且因為掃描永遠重試，拒起**不能每輪都落一次事件**，否則會灌爆帳本。
4. **`/sessions/{id}/lifecycle` 是契約裡的幽靈**（`core_api.yaml:1117`）：
   有 `START|PAUSE|RESUME|END|ROLLBACK` 的描述字串，**沒有 security、沒有 schema、
   沒有任何實作**。它長得就像本卡想要的端點，而那正是陷阱——採用它等於繼承一個
   未認證的規格殘骸。實際的每局控制早就在 `POST /sessions/{id}/control`。
   → **本卡刪掉這個殘骸**，並在 worklog 說明。

### 既有缺陷（本卡必須碰的，因為銷毀模式直接踩在上面）

5. **`delete_session` 的刪除清單已經過期**：`lobby/service.py:445` 少了
   **Message / Request / FirePlan / FirePlanTarget**。這四張表的 `sessionId` 在 prisma
   裡**沒有 FK**（`schema.prisma:263/282/450/468`），所以不會噴 FK 錯——
   資料就這樣**永遠孤兒化**。對一個「銷毀模式」而言這不是疏漏，是**資料殘留**。
6. **`delete_session` 完全不清 Redis**。整局的活狀態都在 `session:{id}:*`
   （hot state、broadcast ring、live_ammo/position/msel、ai_config/ai_status）。
7. **正式部署的帳號對 `TacticalEventLog` 沒有 DELETE 權**
   （`ops/tools/grant_ledger_readonly.sql:22`，dev compose 跑 root 所以本機不會踩到）。
   既有的 `delete_session` 就已經有這個潛在失敗。

### 其他要注意的

8. **階段轉移不可寫 `TacticalEventLog`**。那是 golden 會驗的雜湊鏈；階段推進是牆鐘的、
   人為的、局外的事件，寫進鏈裡會擾動重播。SPEC 說「專屬 audit 表」正是為此。
9. **`clone_session` 會掉七個想定衍生欄**（msel/roe/mobilityOverrides/noStrikeZones/
   requestQuotas/indirectFireRequiresApproval/survivabilityMove）。B1 要「一個演習掛多個
   預推局」，若用 clone 實作，預推局會**沒有 MSEL、沒有 ROE、沒有禁射區**地跑。
   → 本卡**不建在 clone 上**（掛既有 session 即可）；clone 的缺陷記 Backlog。
10. **沒有嚴格 ADMIN 閘門**。`is_omniscient` ＝ {DIRECTOR, WHITE_CELL, ADMIN}，
    三份幾乎相同的角色集散在三個檔案。規格的「需管理員權限」若照 `is_omniscient` 寫，
    等於每個白軍幕僚都能銷毀資料。
11. 前端**沒有任何 PrimeVue Tabs 用例**；全 app 唯一的分頁是 `C2Panel.vue:124` 的
    手工按鈕。且 `lobby.vue` 的四個 e2e testid 被 `auth.spec` / `map.spec` 斷言——
    既有清單若被藏到非預設分頁後面，那些測試會在沒改一行測試碼的情況下轉紅。

## 切卡（紅線 5：一次一張）

規格的 WP-B1 底下實際上是三件事，故切為：

- **B1a**（本次）：`Exercise` 表 + 階段機 + checklist 勾稽 + 專屬 audit 表 + 掛載/卸載 session + 契約。
- **B1b**：撤收建檔（bundle）與銷毀模式——**含修好第 5/6 點**（那是銷毀模式的前提）。
- **B1c**：lobby 演習分頁。

WP-B4（參數簽證）另開卡，接在 B1a 的階段機上。

## 執行紀錄

### B1a 完成（後端 + 契約）

**檔案**

| 檔案 | 動作 | 說明 |
|---|---|---|
| `contracts/core_api.yaml` | 改 | `/exercises*` 九個端點 + 八個 schema + 四個錯誤碼；`SessionSummary` 增 `exercise_id`/`session_role`；**刪掉 `/sessions/{id}/lifecycle` 規格殘骸** |
| `db/prisma/schema.prisma` + 兩個 migration | 改/新增 | `Exercise` / `ExerciseAuditLog` / `ExercisePhase` / `SessionRole`；`WargameSession` 兩個 nullable 欄 |
| `core/app/exercise/phases.py` | 新增 | 階段機與勾稽（**純函數**，不碰 DB） |
| `core/app/exercise/service.py` | 新增 | 建/推/勾/掛/卸 + 稽核 |
| `core/app/exercise/schemas.py`、`core/app/api/exercises.py` | 新增 | 載荷與 router（router 不做授權，一律委派 service） |
| `core/app/errors.py` | 改 | 四個新錯誤類 |

**測試**：`test_exercise_phases.py`（10）、`test_exercise_api.py`（17）、
`test_exercise_contract_conformance.py`（3）。

### 做的過程中改變了設計的三件事

1. **稽核軌跡原本以 `(at, id)` 排序——那是隨機排序**。`at` 的精度救不了同一個請求裡連續
   寫入的兩筆（勾稽 → 推階段），而 uuid 當 tiebreak 等於擲骰子。順序隨機的稽核軌跡
   讀不出因果，等於沒有稽核。**是測試逼出來的**：`test_every_mutation_leaves_a_trace`
   斷言第一筆是 `EXERCISE_CREATED`，一跑就紅。加了演習內單調 `seq`（`TacticalEventLog`
   早有同款前例）。
2. **契約一致性測試自己差點假綠**。`_app_ops()` 原本走 `app.routes`——但這個 FastAPI 版本
   把 include 進來的 router 包成 `_IncludedRouter` 而**不攤平**，逐個讀 `path` 靜靜回空集合。
   「實作有、契約沒有」那條於是恆真。改讀 `app.openapi()`（那也才是 client 真正看到的）。
   兩條斷言只有一條會因為空集合而轉紅——另一條會永遠綠。
3. **`_apply_tick` 整包重指派而非就地改**。`checklist_json` 是 JSON 欄，SQLAlchemy 偵測不到
   巢狀 list/dict 的就地變更，改了不會落盤（本 repo 的既有陷阱）。

### 明確的裁示

- **ADMIN 看得到、推不動**。系統管理不是統裁（`faction_filter` 的既有裁示）。
  非全知者一律回 **404 而不是 403**——403 會回答「這個 id 存在」，那是列舉的入口。
- **刪演習 ≠ 銷毀資料**。掛在底下的局只是 `exercise_id` 轉 NULL。把兩者綁在一起，
  「我按錯了想刪掉這個空專案」會變成刪掉整場演習的資料。
- **掛 session 用既有的局，不用 `clone_session`**。那條路徑會掉七個想定衍生欄，
  預推局會沒有 MSEL、沒有 ROE、沒有禁射區地跑（記入 Backlog）。
- **階段不可倒退**。WP-B4 的簽證與稽核軌跡的意義都來自單調；要重來就開新演習。


### B1b 完成（撤收建檔 + 銷毀模式）

**新檔**：`core/app/exercise/archive.py`（歸檔封包）、`core/app/lobby/purge.py`（資料清除）。
**新端點**：`GET /exercises/{id}/bundle`、`POST /exercises/{id}/destroy`。
**測試**：`test_exercise_archive.py`（10）。

#### 單一 JSON 信封，不做 zip

repo 裡**完全沒有任何 zip/stream/attachment 機制**（`zipfile`/`tarfile`/`gzip`/
`StreamingResponse`/`FileResponse`/`Content-Disposition` 在 core 與 ops 底下一個都搜不到），
而前端唯一的下載路徑 `useAar.ts::aarExportDownload` 走 `apiFetch`——**它會 parse body**，
拿二進位直接壞。為一張卡引進整套二進位下載管線，代價遠大於它買到的東西。

#### 帳本要「原樣」，AAR 要「投影」——不可混用

- 歸檔的帳本用 `TacticalEventLog` 依 seq 的原樣。用 `aar/events.read_events` 會少掉
  被回滾棄置的世代（ADR 007 邏輯截斷），於是產出一條 **`verify_chain` 必定拒絕**的鏈
  ——歸檔出一份驗不過的證據是最糟的結果。鏈驗結果**一起寫進封包**：
  事後有人問「這份資料可信嗎」，答案要在封包裡，不是要對方自己再跑一次工具。
- AAR 統計用 `read_events` 的投影：那才是「實際發生過什麼」的時間軸。

#### 匯出原本會改變自己的雜湊

`content_hash` 要能當「歸檔後有沒有被動過」的比對基準。但匯出會寫一筆
`BUNDLE_EXPORTED` 稽核，而稽核軌跡也在封包裡——**每匯出一次，下一次的雜湊就變一次**，
於是雜湊失去它存在的唯一理由。**是測試逼出來的**（`test_bundle_hash_is_stable_across_calls`）。
修法：匯出紀錄不進封包。封包記錄的是「這場演習怎麼進行的」；
「誰在什麼時候把資料帶走」屬存取紀錄，留在 `/audit`（那份不會被帶走）。

#### 銷毀模式的三道閘門

1. **限 ADMIN**——`is_omniscient` 包含每一位白軍幕僚，用它等於把不可逆的銷毀
   開放給整個統裁組。這是 repo 裡第一個嚴格 ADMIN 閘門。
2. **必須已 ARCHIVED**——要求先走完階段機，就保證了「該匯出的有機會匯出」。
3. **`confirm_name` 逐字相符**——二次確認若只是「再按一次是」，那不是確認、是多按一次。

演習專案與稽核軌跡**留下來**：「這場演習存在過、被誰在何時銷毀」正是稽核要保留的東西。

#### 修掉的既有 bug：刪除推演會留下孤兒

`delete_session` 的刪除清單是手寫的，而它已經漏了 **Message / Request / FirePlan /
FirePlanTarget**——這四張在 prisma 裡沒有 FK，所以不會噴錯，列就這樣**永遠孤兒化**。
對「刪除推演」而言那是遺漏；對銷毀模式而言，那是**資料殘留**。
另外 `delete_session` **完全不清 Redis**（整局活狀態都在 `session:{id}:*`）。

改成由 SQLAlchemy 的 **mapper registry** 自省導出。
⚠ 第一版寫的是 `app.models.__all__`——而 `Message`/`Request` **根本不在 `__all__` 裡**，
那份「自省」會漏掉的剛好就是要修的那幾張。**自省若建在另一份手寫清單上，
它只是把手寫清單換了個地方藏。**（順手把兩個模型補進 `__all__`。）

⚠ **已知界線**：正式部署的應用帳號對 `TacticalEventLog` 沒有 DELETE 權
（`grant_ledger_readonly.sql`，帳本 append-only 是刻意的防線）。這個限制在既有的
`delete_session` 就存在；本卡**不繞過它**，真要銷毀帳本得由 DBA 依 runbook 執行。
dev compose 跑 root 故本機不會踩到——**這代表本機測不出那條路徑**，如實記在這裡。
