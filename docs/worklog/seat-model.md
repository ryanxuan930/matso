---
task: WP-B5.1        # SPEC_V2 §6 WP-B5 第一張卡（席位模型）
status: DONE
started: 2026-07-30T21:30+08:00
updated: 2026-07-30T22:20+08:00
agent: Opus 5
---

# WP-B5.1 席位模型（seat_role）

## 目標摘要

RBAC 目前是「角色 × 陣營」，同一陣營內所有人權限相同。演習實務是**同陣營多席分工**
（[JCATS-F p.9–10] 每指揮組/排長/觀測官一席）。本卡把 `SessionParticipant` 加上
`seat_role`（COMMANDER / S2_INTEL / S3_OPS / FSO_FIRES / S4_LOG / OBSERVER），
下令權按席位細分。這是 WP-B5（申請-核覆與 C2 信文）與 C10（臨機火力鏈）的前置。

## 最重要的設計前提：既有局零行為變更

`seat_role` **可為 NULL，NULL ＝ 沿用現行的角色權限（不縮也不放）**。

理由：這個 repo 已經有正在跑的推演局與參與者，若上線即套用席位矩陣，
所有既有參與者會突然變成「沒有席位 → 不能下令」，等於把跑到一半的演習鎖死。
既有局零遷移是硬性要求（同 #98 關係矩陣那次的做法：欄位可為 NULL＝未宣告）。

**驗收要有一條測試明確釘住這件事**，而不是靠人記得。

## 待確認的設計（先寫下來，實作時逐條回填）

席位 → 可下令型別的對應，規格只寫「按 seat_role 細分」沒有給表。初版取**保守且可解釋**的
對應，並做成資料驅動（單一 registry），讓 B5.2/C10 接手時只改那張表：

| 席位 | 可下的令 | 依據 |
|------|---------|------|
| COMMANDER | 全部 | 指揮官 |
| S3_OPS | 機動類（MOVE） | 作戰官管兵力運用 |
| FSO_FIRES | 火力類（ENGAGE） | 火力支援協調官 |
| S2_INTEL | 無（唯讀情報） | 情報官不下戰術令 |
| S4_LOG | 補給類（RESUPPLY，目前尚無此令型） | 後勤官 |
| OBSERVER | 無 | 觀察員 |

⚠ 這張表是**我依角色職掌推的，不是規格明文**。若與使用者的實務認知不符，改這一張表即可，
不會動到其他地方——這也是做成 registry 的原因。

## 計畫

- [x] 1. 契約先行：`contracts/core_api.yaml` 的 participants schema 加 `seat_role`（可為 null）→ 驗證。
- [x] 2. DB：`db/prisma/schema.prisma` 加 `seatRole SeatRole?` + enum → `prisma migrate`（紅線 4）。
- [x] 3. SQLAlchemy model + `SeatRole` enum；`schema_sync_check` 綠。
- [x] 4. 權限：單一 registry（席位 → 可下令型別），NULL 席位走既有邏輯。
- [x] 5. 下令端點接上 gate；越權回明確錯誤碼（不是泛用 403）。
- [x] 6. 前端：lobby 參與者面板可指派席位。
- [x] 7. 測試（含「NULL 席位行為與改版前完全相同」的回歸釘）+ 四道驗證。

## 執行紀錄

- `21:30` 開卡，盤點現有 `SessionParticipant` 與 RBAC。訂出「NULL ＝ 沿用現行權限」硬性前提。
- `21:5x` **資料層**（`dc8c4f4`）：契約先行（SeatRole enum + View/Assign 加可為 null 的 seat_role，
  openapi-spec-validator 通過）→ `prisma migrate`（紅線 4）→ SQLAlchemy model。
  **實測 migration 後 9 筆參與者全為 NULL**，零遷移確認。
- `22:0x` **使用者裁示**：席位對應採我提的保守表；NULL 維持現狀不縮不放。
- `22:1x` **權限 gate**（`d558f3e`）：`app/seats` 單一 registry；新錯誤碼 `ORDER_SEAT_DENIED`；
  participants API 讀寫 seat_role。6 個測試。
- `22:2x` **前端**：名冊每列加席位下拉（預設「（未指派席位）」）。
  改陣營/角色時會帶上現有 seat_role，否則會把席位洗掉——這點差一步就漏了。
  容器實測往返：設 S3_OPS → DB 落值；改回空 → DB 回 NULL（9/9）。

## 收尾

### 決策紀錄

| 決策 | 選擇 | 理由 |
|------|------|------|
| 席位 → 可下令型別 | COMMANDER 全／S3_OPS 機動／FSO_FIRES 火力／S2·S4·OBSERVER 無 | 使用者確認；收在單一 registry，日後改一張表 |
| 未指派席位 | 完全沿用角色既有權限 | 既有局零行為變更；有專門測試釘住 |
| 錯誤碼 | 另立 `ORDER_SEAT_DENIED` | 「不是你的單位」與「不是你的職掌」在演習中處置不同，合併會讓前端無從區分 |
| 檢查順序 | 陣營/unit_scope → 席位 | 「不是你的單位」更根本，先報比較好懂 |

### 未做（刻意）

- **S4_LOG 目前是空集合**——補給令型還不存在（WP-C7）。空集合＝不能下任何令，
  與「未指派席位」是兩件不同的事，registry 裡沒有合併。
- **審批權未做**：規格的「下令與審批權按 seat_role 細分」中，審批屬 B5.2（申請-核覆），
  本卡只做下令權。
- **前端未依席位隱藏 UI**：COP 仍會顯示下令面板，越權時由後端擋並回 `ORDER_SEAT_DENIED`。
  後端是權威這點沒問題，但體驗上應該讓 S2 席次一開始就看不到下令按鈕——記為 B5.2 的順帶項。

## 中斷續作指引

- **本卡（B5.1）已完成**。下一張是 **B5.2 信文/申請核覆**（`Message` / `Request` 實體 +
  審批鏈），再來是 C10 臨機火力鏈。
- 要調整席位分工只改 `core/app/seats/SEAT_ORDER_TYPES` 一張表，其餘不必動。
- **別動 `test_no_seat_behaves_exactly_as_before`**：它釘住「NULL 席位不設限」，
  那是既有推演局不被鎖死的唯一保證。
