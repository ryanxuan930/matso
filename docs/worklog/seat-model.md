---
task: WP-B5.1        # SPEC_V2 §6 WP-B5 第一張卡（席位模型）
status: IN_PROGRESS
started: 2026-07-30T21:30+08:00
updated: 2026-07-30T21:30+08:00
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

- [ ] 1. 契約先行：`contracts/core_api.yaml` 的 participants schema 加 `seat_role`（可為 null）→ 驗證。
- [ ] 2. DB：`db/prisma/schema.prisma` 加 `seatRole SeatRole?` + enum → `prisma migrate`（紅線 4）。
- [ ] 3. SQLAlchemy model + `SeatRole` enum；`schema_sync_check` 綠。
- [ ] 4. 權限：單一 registry（席位 → 可下令型別），NULL 席位走既有邏輯。
- [ ] 5. 下令端點接上 gate；越權回明確錯誤碼（不是泛用 403）。
- [ ] 6. 前端：lobby 參與者面板可指派席位。
- [ ] 7. 測試（含「NULL 席位行為與改版前完全相同」的回歸釘）+ 四道驗證。

## 執行紀錄

- `21:30` 開卡。讀 SPEC_V2 §6 WP-B5、現有 `SessionParticipant`（model + prisma）、
  既有 participants 端點與 RBAC（O7.5 的 7 角色 × 4 端點矩陣）。
  訂出「NULL ＝ 沿用現行權限」為硬性前提。

## 中斷續作指引

- 尚未動任何程式碼。上面的「席位 → 可下令型別」對應表是我推的、非規格明文，
  動手前值得先跟使用者確認一次。
- 順序有依賴：契約 → prisma migrate → model → 權限 registry → 端點 → 前端。
- **既有局零行為變更**是這張卡最容易被犧牲掉的東西，請保留那條回歸測試。
