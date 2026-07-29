---
task: WP-B5.2        # SPEC_V2 §6 WP-B5 第二張卡（信文 / 申請-核覆）
status: IN_PROGRESS
started: 2026-07-30T22:30+08:00
updated: 2026-07-30T22:30+08:00
agent: Opus 5
---

# WP-B5.2 C2 信文與申請-核覆工作流

## 目標摘要

「指參程序的磨練」靠的是**異步審批鏈與 C2 工件流轉**，不是即時生效的按鈕
（[JCATS-A p.13,15,26]、[JCATS-F p.10–14]）。本卡把兩個新實體接進來：

- `Message`（信文）：席位之間的文書往返，WS 推播給收件席位/陣營。
- `Request`（申請單）：下級席位送出 → 上級/白軍核覆 → 核准後轉為對應效果，並消耗配額。

## 開卡先做的事：切卡

規格在 WP-B5 底下列了五件事，**合起來遠超一張卡**：

1. `Message` 實體 + WS 推播
2. `Request` 實體 + 審批鏈 + 配額
3. 曲射火協 gate（`indirect_fire_requires_approval`，接 precheck）
4. 標繪分送（`MapFeature.shared_to: seat[]`）
5. 殲敵自動 REPORT 信文（防重複打擊）

roadmap 那條鏈寫的是 `B5.1 → B5.2 信文/申請核覆 → C10`，所以 **3–5 不在 B5.2 的字面範圍內**。
本卡只做 1+2（信文與審批鏈本身），並把 3–5 明確留給後續卡片：

| 卡 | 範圍 |
|----|------|
| **B5.2（本卡）** | `Message` + `Request` + 審批鏈 + 配額 + WS 推播 + API + UI |
| B5.3 | 曲射火協 gate（precheck 掛已核准的 FIRE_SUPPORT request） |
| B5.4 | 標繪分送 + 殲敵自動 REPORT |

理由與 G1 拆成 G1a/G1b 相同：一次收完不利驗證，而且審批鏈本身就有足夠的狀態機要釘。

## 設計要點（動手前先想清楚的）

### 1. WS 受眾要從「陣營」擴到「席位」

現有 `stream/faction_filter.py` 只認 `faction` / `factions` / `exclusive`
（WP-C5 加的）。信文的受眾是**收件席位**——同陣營內只有那一席該收到。
需在 envelope 加席位受眾，且**過濾一律在後端**（紅線 3）。

⚠ 這是本卡唯一會動到既有傳輸層的地方，要特別小心不要放寬既有的陣營過濾。

### 2. 配額用什麼時間軸

規格說「每日（模擬日）配額」。目前 `SimClock` 只有 tick，沒有「模擬日」概念。
先以**想定定義的配額總量 + 已消耗計數**實作（不做每日重置），
把「每日重置」記為待辦——沒有模擬日曆之前做不出正確語意，硬做會是假的。

### 3. 審批權來自席位（B5.1 的延伸）

誰能核覆＝`SEAT_APPROVAL`（新 registry，與 `SEAT_ORDER_TYPES` 並列）。
**未指派席位（NULL）依既有角色規則**——與 B5.1 同一條原則，不能在這裡破例。

## 計畫

- [x] 1. 契約先行：`Message` / `Request` schema + 端點 → 驗證。
- [x] 2. DB：prisma schema 兩張新表 + enum → migrate。
- [x] 3. SQLAlchemy models + schema_sync。
- [x] 4a. 審批 registry（誰能核覆）。
- [ ] 4b. 服務層狀態機（PENDING→APPROVED/DENIED→EXPENDED）+ 配額。
- [ ] 5. API：送信 / 收信匣 / 送出申請 / 核覆 / 查詢。
- [ ] 6. WS：席位受眾 + 推播（後端過濾，紅線 3）。
- [ ] 7. 前端：信文匣 + 申請/核覆 UI。
- [ ] 8. 測試（含越權核覆被拒、配額用罄自動 DENIED、NULL 席位沿用既有規則）+ 四道驗證。

## 執行紀錄

- `22:30` 開卡，訂切卡方式（本卡只做信文 + 審批鏈）與三個設計要點。
- `22:4x` **契約先行**：9 個 schema（MessageKind/RequestKind/RequestStatus/MessageView/
  RequestView/配額…）+ 3 個端點 + 3 個錯誤碼，openapi-spec-validator 通過。
- `22:5x` **DB**（`d18c46a`）：`Message` / `Request` 兩張表 + 三個 enum → prisma migrate
  （18 tables / 170 columns）。SQLAlchemy models 補上，schema_sync 綠。
  中間踩到一個小的：`readAt` 在 prisma 是 nullable、SQLAlchemy 端漏了 `| None`，
  schema_sync_check 直接抓出來——那個關卡是有用的。
- `23:0x` **核覆權 registry**（`b340b2e`）：`app/c2` 的 `SEAT_APPROVAL`，
  與 B5.1 的 `SEAT_ORDER_TYPES` 並列。11 個測試。

## 目前狀態（給接手的人）

**已完成**：契約、DB、models、核覆權 registry（純函數 + 測試）。
**未完成**：服務層狀態機 + 配額、API 端點、WS 席位受眾、前端。

### 已經想清楚、照著做即可的部分

- **狀態機**：PENDING →（核覆）→ APPROVED / DENIED；APPROVED →（用掉）→ EXPENDED。
  轉移一律經單一函數，非 PENDING 再核覆要拋 `RequestAlreadyDecidedError`（409）。
- **配額用罄的處理**：規格說「配額用罄後申請自動 DENIED」——**不是拒收**。
  差別很重要：留痕才看得出這個陣營在第幾 tick 被配額卡住，那正是 AAR 要評的東西。
- **送出申請時附言**要一併生成一封 `REQUEST` 信文（`ref_id` 指向申請單），
  核覆時生成 `APPROVAL` 信文——信文才是 C2 工件流轉的載體，申請單只是狀態。

### 還沒解的設計問題

- **WS 席位受眾**：`stream/faction_filter.py` 目前只認 `faction` / `factions` / `exclusive`。
  信文要送到「某陣營的某一席」，需要新的受眾維度。
  ⚠ 那支檔案是紅線 3 的守門處，加席位受眾**不得放寬既有的陣營過濾**，
  WP-C5 的 `exclusive` 語義也要保住。動它之前先把現有 4 條分支的真值表寫出來。
- **配額來源**：想定層目前沒有 `request_quotas` 欄位。要嘛加進 scenario schema
  （得動 O7.1 的 loader 與驗證），要嘛先放 session 層。**還沒決定**。

## 中斷續作指引

- 下一步是 **4b 服務層狀態機 + 配額**，再來 API → WS → 前端。
- 動 `faction_filter.py` 前務必先讀上面的警告。
- 本卡刻意不做：曲射火協 gate（B5.3）、標繪分送與殲敵自動回報（B5.4）。
- 已知界線：配額是**整局總量非每日**（SimClock 無模擬日概念），已寫進契約說明。
