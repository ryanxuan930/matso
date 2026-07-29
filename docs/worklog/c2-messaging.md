---
task: WP-B5.2        # SPEC_V2 §6 WP-B5 第二張卡（信文 / 申請-核覆）
status: DONE
started: 2026-07-30T22:30+08:00
updated: 2026-07-30T23:50+08:00
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
- [x] 4b. 服務層狀態機（PENDING→APPROVED/DENIED→EXPENDED）+ 配額。
- [x] 5. API：送信 / 收信匣 / 送出申請 / 核覆 / 查詢。
- [x] 6. WS：席位受眾 + 推播（後端過濾，紅線 3）。
- [x] 7. 前端：信文匣 + 申請/核覆 UI。
- [x] 8. 測試（含越權核覆被拒、配額用罄自動 DENIED、NULL 席位沿用既有規則）+ 四道驗證。

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

## 收尾（B5.2 完成）

| 步驟 | commit |
|------|--------|
| 契約 + DB + models | `d18c46a` |
| 核覆權 registry | `b340b2e` |
| WS 席位受眾（先釘 14 格真值表再改） | `0db7704` |
| 想定層配額 + 開局快照 | `58bcb36` |
| 服務層狀態機 + 配額 | `0165c0e` |
| REST 端點 + 收信匣不外洩 | `16fe096` |
| 前端小工具 + COP 顯示席位 | `6601916` |
| WS 即時推播接線 | `3ad777f` |

### 使用者裁示（2026-07-30）

| 問題 | 選擇 |
|------|------|
| WS 席位受眾 | 加 `seat` 欄位、**只能收窄** |
| 配額來源 | 想定層 `request_quotas`（開局快照） |

### 幾個刻意的設計，各有測試釘住

1. **配額用罄落 DENIED，不是拒收**——留痕才看得出在第幾 tick 被卡住，那是 AAR 要評的。
2. **PENDING 也佔配額**——否則 4 架次可先送 10 張單再一路核准。
3. **APPROVED ≠ EXPENDED**——一張核准單只能兌現一次，合併會讓同一張火協掛在兩次砲擊令上。
4. **NULL 席位沿用角色規則**——與 B5.1 同一條原則，否則既有局沒有人能核覆。
5. **席位只能收窄**——`_faction_visible` 先過才看席位；新維度若能單獨放行等於開旁路。

### 動守門處的做法（值得重複）

`faction_filter.py` 是紅線 3 的唯一閘門。動它之前先寫
`test_stream_audience_truth_table.py` 把**現有 14 條分支逐格釘死**，跑綠才改程式碼。
**不測「應該長怎樣」，測「現在就是這樣」**——任何一格從 False 變 True 就是漏敵情。
改完 14 格原封不動，另加 7 格席位案例（含「席位相同但陣營不同」與
「exclusive + 席位相符」兩個會漏的情況）。

### 自己抓到的一條假測試

原本的 `test_inbox_does_not_leak_across_seats` **只驗了寄件人自己看得到**——
而寄件人一定看得到（寄件備份），這條測試會永遠通過，即使席位過濾完全失效。
重寫成有第三個人的版本（指揮官發給 FSO 席，斷言 S2 席收到空的）才真的驗到邊界。
**名字說一套、做另一套的測試比沒有測試更危險。**

### 驗證

- pytest 1375、mypy 217、ruff、schema-sync（18 tables / 171 columns）全綠。
- 前端 lint / typecheck / build 綠。
- **容器實測完整往返**：送信 → 信文匣；送出空偵申請 → 待核覆、配額 1；
  核准 → 已核准，信文匣自動多出「申請（→指揮官席）」與「核覆」兩封。測試資料已清除。
- **e2e（`.env`-free worktree）**：4 failed / 18 passed，與 D6.1 收卡時**逐條相同**，零回歸。

### 未做（明確留給後續卡）

- **B5.3 曲射火協 gate**：`indirect_fire_requires_approval` + precheck 掛已核准的
  FIRE_SUPPORT request。`expend_request` 已備好（非 APPROVED 回 None），接上即可。
- **B5.4 標繪分送 + 殲敵自動 REPORT**：`MapFeature.shared_to: seat[]`。
- **每日配額重置**：目前是整局總量。SimClock 沒有「模擬日」概念，硬做會是假的。
  要做得先有模擬日曆（與 C4 環境演進相關）。
- **前端未依席位隱藏下令 UI**：越權由後端擋並回明確錯誤碼；體驗上 S2 席不該看到下令按鈕。

## 中斷續作指引

- **本卡（B5.2）已完成**。下一張照 roadmap 是 **C10 call-for-fire**，但它依賴 B5.3 的火協 gate，
  建議先做 B5.3（範圍小、`expend_request` 已備好）。
- 要調整席位分工/核覆權只改兩張表：`app/seats/SEAT_ORDER_TYPES`、`app/c2/SEAT_APPROVAL`。
- **動 `faction_filter.py` 一律先跑 `test_stream_audience_truth_table.py`**，那是紅線 3 的護欄。
