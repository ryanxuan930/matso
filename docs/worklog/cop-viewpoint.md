---
task: "#90 COP 視角切換（全知者可套各陣營戰場迷霧）"
status: DONE
started: 2026-07-28T14:20+08:00
updated: 2026-07-28T15:05+08:00
agent: Opus 5
spec: SPEC_FULL §12（RBAC/視角）、§13.3（fog of war）、§7.2（偵測）
---

# #90 COP 視角切換

## 目標
白軍/管理員可在 COP 選「全局視角」或「某陣營視角」；選了陣營即以該陣營之眼觀戰，
套用其戰場迷霧（看得到的才看得到）。此視角亦是 #91（友/敵符號）與 #92（標註歸屬）的觀測者基準。

## 動工時發現的真正缺口
不是「加個下拉」而已——**COP 從來沒有呼叫過 `/intel`**。原本的敵情是這樣來的：

```
realAsContacts = realUnits.filter(u => u.faction !== myFaction)   // 從 /units 反推
```

這有兩個致命問題：
- **一般陣營角色**：`/units` 只回己方 → 過濾後恆為空 → **COP 上完全沒有敵人**。
- **白軍**：`/units` 回全部 → 等於把 ground truth 當敵情（且因 `myFaction` 為空，
  全部被當友軍畫成同一種符號——使用者截圖裡三個陣營都是黃色菱形就是這樣來的）。

另外 **`/intel` 端點從未寫進契約**（後端 `api/intel.py` 早就有，`core_api.yaml` 沒有），
故依紅線 #4 先補契約再實作。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `contracts/core_api.yaml` | 修改 | +`/sessions/{id}/intel`（含 `as_faction`）、+`ContactView` schema（明載去識別化語義） |
| `platform/app/types/api.ts` | 重生 | `npm run gen:api` |
| `platform/app/composables/useIntel.ts` | 新增 | `fetchIntel`（帶 as_faction）、`toContact`（ContactView→地圖 Contact） |
| `platform/app/composables/useOrders.ts` | 修改 | `fetchUnits` 加 `asFaction` 參數 |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `viewpoint` + `observerFaction` + 敵情改取 `/intel`；視角下拉；切換即重抓 |

**紅線遵守**：`viewpoint` 只是帶給後端的 `as_faction`，**前端不做任何可見性過濾**；
越權由後端擋（一般角色帶他陣營 → 403，既有測試 `test_commander_cannot_view_other_faction`）。

## 設計決定
- **`observerFaction = viewpoint || myFaction`**：單一概念，#91/#92 都以它判「我方」。
- **只換顯示/互動的判我方（5 處），不動權限判斷**：`canControl` 已涵蓋白軍，權限仍綁 `myFaction`
  以免視角變成提權途徑。
- **視角下拉選項只在全局視角時更新**：切了視角後 `/units` 只回該陣營，若跟著更新會把清單縮成一項。
- **切換視角時清掉選取/鎖定目標**：那是前一個視角的東西，留著會指向現視角看不到的單位。

## 測試證據
- gates：`pytest` **1067 passed / 8 skipped**（golden 6 未破）、ruff/mypy(202)/schema-sync/buf、
  前端 lint + typecheck 全綠。
- **瀏覽器實測（複製一局跑，驗完刪除，未動使用者原局）**：
  - 下拉四項：全局 + BLUE/RED/YELLOW；切換後**選項不縮**。
  - 切 RED → 單位清單只剩 RED 13。
  - 後端實收（core log）：`units?as_faction=RED` 與 `intel?as_faction=RED` **兩者都帶**。
  - **地圖圖徵：13 `own` + 22 `contact`** ＝ RED 自有單位 + RED 偵測所得（非 ground truth）。
  - 戰況事件面板出現 `SENSOR_CONTACT`。

## 已知限制（誠實記錄）
- **`relation` 目前一律標 HOSTILE**：該局關係矩陣執行期取不到（見 #98）。友軍要顯示為 Friendly
  需先解那張卡——這正是 #91 的前置。
- **陣營視角下無法下 ENGAGE 令**：`engageTargets` 取自 `/units`，切視角後只剩己方。
  要能打 contact 得由後端做 contact→unit 解析（且需考量是否洩漏 ground truth），另案。
  註：一般陣營角色**本來就**是這個狀態（`/units` 只回己方），故非本卡造成的退步。
- 地圖標註尚未帶 `as_faction`（#92 範圍）。

## 中斷續作指引
- **下一步第一件事**：#98 關係矩陣持久化（`WargameSession` 加欄位 → loader 寫入 → 執行期讀取 →
  API 透出），完成後才做 #91。
