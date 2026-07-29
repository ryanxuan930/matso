---
task: WP-C10.1       # SPEC_V2 §6 WP-C10 第一張卡（臨機火力申請）
status: DONE
started: 2026-07-31T01:00+08:00
updated: 2026-07-31T01:30+08:00
agent: Opus 5
---

# WP-C10.1 臨機火力申請（call-for-fire）

## 開卡先切卡

WP-C10 規格涵蓋 [JCATS-F p.12–13] 整個第肆章，內容遠超一張卡：

1. 臨機火力鏈：觀測單位 → CALL_FOR_FIRE → FSO 核准 → 砲兵執行
2. `FirePlan` 實體 + `at_tick` 排程（接 MSEL）+ `on_call`
3. BDA 回報（帶迷霧誤差——BDA 是情報不是 ground truth）
4. 散布係數掛「觀測者是否存在」（前觀陣亡 → 散布加倍）
5. 陣地變換 `survivability_move`

切為：

| 卡 | 範圍 |
|----|------|
| **C10.1（本卡）** | `CALL_FOR_FIRE` 申請種類 + **觀測者條件**（申請者須有單位對目標有 LOS）+ 重用 B5.2 審批鏈 |
| C10.2 | `FirePlan` 實體 + at_tick/on_call 排程 |
| C10.3 | BDA 回報 + 散布係數掛觀測者 |
| C10.4 | 陣地變換 survivability_move |

> ⚠ **上表的編號已過時**：C10.2 後來改成「面目標射擊」（FirePlan 缺的是能力不是包裝，
> 見 `area-fire.md`），其餘各卡順延一號。現行編號以 SPEC_V2 §WP-C10 的表為準。

**本卡刻意做得很薄**，因為 B5.2 已經把審批鏈、配額、留痕、WS 推播全做完了——
C10.1 真正新增的只有「這張申請單合不合法」這一條規則：**沒有觀測就不能叫火力**。
那正是 [JCATS-F p.12] 觀測所在整條鏈裡的角色。

## 設計要點

### 觀測條件的判定放在哪

放在**送出申請時**（`submit_request` 的呼叫端），不是核覆時：
FSO 席看到的申請單應該都是「觀測上成立」的，把不成立的擋在更前面才不會浪費核覆者的注意力。
且申請當下的觀測狀態才是有意義的——核覆時目標可能已經移出視線，那是另一回事。

### LOS 用哪一條

**必須與交戰預檢共用同一條 LOS**（`PhysicsGateway`），不另寫一套。
兩份 LOS 實作就是兩份會漂移的物理——這個 repo 已經有 fog of war 因此出事的前例（WP-C5）。

## 計畫

- [x] 1. `CALL_FOR_FIRE` 加入 `RequestKind`（contract + prisma migrate + enum）。
- [x] 2. 觀測條件：申請 params 帶 `target_contact_id` 或 `target_latlng`；
      驗證申請者陣營有任一單位對該點有 LOS（共用 PhysicsGateway）。
- [x] 3. 端點接線 + 錯誤碼 `REQUEST_NO_OBSERVER`。
- [x] 4. 測試（含「沒有觀測就不能叫火力」與「未帶目標 → 擋下」）。

## 執行紀錄

- `01:00` 開卡並切卡。確認 B5.2 已把審批鏈做完，本卡只補「觀測條件」這條規則。
- `01:30` 實作完成。4 個 API 測試（未帶目標/無觀測/有觀測/其他種類不受影響）。

## 收尾

pytest 1390、mypy 217、ruff、schema-sync（18 tables / 172 columns）全綠。

### 自己被自己的 `except Exception` 打臉

`has_observer_on` 原本寫了 `except Exception: continue`，理由是「terrain 不可達不該讓申請爆掉」。
結果測試裡一個 `LosOutcome` 建構子筆誤被吞掉，**看起來變成「沒有觀測」**——
花了一輪才看出不是邏輯錯。

改掉了：**不吞例外**，讓 `TerrainUnavailableError` 往上拋（API 轉 503）。
「系統故障」與「戰術上沒有觀測」對使用者的意義天差地遠——
前者該修系統，後者該換觀測位置。混在一起會讓演習中的人往錯的方向想。

### 為什麼 CALL_FOR_FIRE 與 FIRE_SUPPORT 分開

- `FIRE_SUPPORT`（B5.2）：**授權**——「解鎖一次曲射任務」，掛在 ENGAGE 令上（B5.3 的 gate）。
- `CALL_FOR_FIRE`（本卡）：**任務單**——「我看到目標、請對這裡射擊」，帶目標座標且須有觀測。

合併會讓「有權開火」與「有目標可打」變成同一件事，那正是火力協調要分開的兩件事。

## 中斷續作指引

- **本卡（C10.1）已完成**。下一張：C10.2（`FirePlan` + at_tick/on_call 排程）。
- 本卡**只做到申請受理**：核准後尚未自動生成 ENGAGE 令、也還沒指派砲兵單位——那屬 C10.2。
- 前端尚無 call-for-fire UI（要能從 COP 點目標→帶座標送單）。C10.2 一併做。
