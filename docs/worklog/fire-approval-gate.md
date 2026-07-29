---
task: WP-B5.3        # SPEC_V2 §6 WP-B5 第三張卡（曲射火協 gate）
status: DONE
started: 2026-07-31T00:10+08:00
updated: 2026-07-31T00:45+08:00
agent: Opus 5
---

# WP-B5.3 曲射火協 gate

## 目標摘要

想定開關 `indirect_fire_requires_approval`：開啟時，ARTILLERY/MISSILE 的 ENGAGE 令
必須掛一張**已核准的 FIRE_SUPPORT 申請單**，否則預檢拒絕。核准單用掉即轉 EXPENDED
（B5.2 的 `expend_request` 已備好：非 APPROVED 一律回 None）。

## 設計要點

### 1. 執行點是「下令准入」，不是每 tick 裁決

WP-B6 的 ROE 武器禁令有兩個執行點（precheck 早退 + 裁決層逐武器篩），
因為那是**每陣營的常規**，每一發都要篩。

火協 gate 不同：它是**針對單一道令的許可**。令被擋在 submit，就永遠到不了裁決層。
所以放在 precheck，且**不需要**在裁決層再做一次——這不是偷懶，是兩者性質不同。

### 2. 「不指名武器」的漏洞必須堵

ROE 的 `_precheck_roe_weapon` 只擋「令面指名了被禁武器」，其餘交給裁決層逐武器篩。
火協 gate 若照抄這個做法，**不指名武器就能繞過** ——那就不是 gate。

故判定為：本局要求火協 **且**（指名的武器是曲射 **或** 未指名武器但該單位持有任何曲射武器）
→ 需要已核准的申請單。

混合編裝（步槍 + 迫砲）的單位在未指名武器時會被擋——這是**刻意的**：
在要求火協的演習裡，要用直射就把武器指出來。錯誤訊息會講清楚該怎麼做。

### 3. 何時 EXPEND

核准單在**令通過預檢、確定收下時**兌現（不是在裁決命中時）。理由：一張核准單對應
「一次火力任務」的授權，令被接受就是授權被使用；若等到命中才扣，令被取消或未命中時
授權會憑空復活。

## 計畫

- [x] 1. 想定 schema 加 `indirect_fire_requires_approval` → 驗證。
- [x] 2. prisma：`WargameSession.indirectFireRequiresApproval` → migrate；loader 開局快照。
- [x] 3. `EngagePayload` 加 `fire_request_id`（契約先行）。
- [x] 4. precheck gate + 新錯誤碼 `ORDER_FIRE_APPROVAL_REQUIRED`。
- [x] 5. 令收下時 EXPEND 核准單。
- [x] 6. 測試（含未開關時零行為變更、不指名武器不得繞過、核准單只能用一次）。

## 執行紀錄

- `00:10` 開卡。讀 `_precheck_roe_weapon`（樣板）、`engage_wiring` 的武器類別、
  `sim_runtime` 的 ROE 注入路徑。訂出上面三個設計要點——其中第 2 點是這張卡的關鍵：
  照抄 ROE 的做法會留下一個「不指名武器就繞過」的洞。
- `00:45` 實作完成。10 個測試，重點三條：
  **不指名武器不得繞過**（本卡的關鍵）、**未開開關零行為變更**、**EXPENDED 不得重用**。
  另擋「拿別陣營的核准單」與「拿空偵單當火協用」。

## 收尾

pytest 1385、mypy 217、ruff、schema-sync（18 tables / 172 columns）、OpenAPI 驗證全綠。

### 與 ROE 的關鍵差異（本卡最該記住的）

`_precheck_roe_weapon` 只擋「令面**指名了**被禁武器」，其餘交裁決層逐武器篩——
因為 ROE 是**每陣營的常規**，每一發都會被篩。

火協 gate 是**單一道令的許可**，令被擋在 submit 就到不了裁決層。若照抄 ROE 的做法，
**不指名武器就能繞過**。故本檢查在「未指名武器但單位持有任何曲射武器」時同樣要求核准單。
混合編裝未指名武器時會被擋，是刻意的——訊息明講「請附核准單或指名直射武器」。

### 核准單何時兌現

在**令通過預檢、確定收下時**（`OrderService.submit`），不是裁決命中時。
一張核准單對應「一次火力任務」的授權；令被接受＝授權已使用。
若等命中才扣，令被取消或未命中時授權會憑空復活。

## 中斷續作指引

- **本卡（B5.3）已完成**。下一張是 **C10 call-for-fire**（前置已備齊）或 **B5.4 標繪分送**。
- 前端尚未提供「選擇已核准的火協申請單」的 UI——目前要靠 API 直接帶 `fire_request_id`。
  C10 會需要那個 UI，屆時一併做。
