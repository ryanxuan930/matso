---
task: "#91 友軍/敵軍 2525 affiliation 依關係矩陣"
status: DONE
started: 2026-07-28T16:20+08:00
updated: 2026-07-28T17:05+08:00
agent: Opus 5
spec: SPEC_FULL §12.1（關係矩陣）、§13.3（fog of war 符號）
---

# #91 我方/友軍畫 Friendly、敵方畫 Enemy

## 前置
`affiliationForRelation`（F/N/H）與 `sidcForContact`/`sidcForOwnUnit` **早就寫好**；缺的是
「觀測者對該陣營的關係」這個輸入。#98 讓關係在後端活著、#90 定義了 `observerFaction`，本卡把兩者接起來。

## 動工才發現的連帶缺口：盟軍會互相隱形
`intel/sweep.py` 的 docstring 寫著「己方與 ALLIED 不成 contact（**盟軍經共享視圖，非偵測**）」，
但那個「共享視圖」**從來沒有實作**——`list_units` 一直是嚴格 `faction == observer`。

#98 把關係矩陣接上之後，盟軍變成：**不在 units（嚴格等值過濾）、也不在 contacts（sweep 排除盟軍）**
＝ 完全隱形。若不一併補上，#91 的「友軍用 Friendly 顯示」根本沒有東西可顯示。
故本卡把該共享視圖補上（`_visible_factions` ＝ 自己 + ALLIED）。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `contracts/core_api.yaml` | 修改 | +`/sessions/{id}/relations`（含 as_faction）、+`FactionRelationsView` |
| `core/app/api/relations.py` | 新增 | **以觀測者為中心**的關係查詢（見下） |
| `core/app/api/units.py` | 修改 | `_visible_factions`：自己 + 盟軍（共享視圖） |
| `core/app/main.py`、`api/__init__.py` | 修改 | 註冊 router |
| `platform/app/composables/useIntel.ts` | 修改 | +`fetchRelations` |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `relationOf`/`isFriendly`；contacts 帶真關係；友軍列入 `realAsOwn`；**友軍不可被鎖為交戰目標** |

## 設計決定
- **只回「以觀測者為中心的一列」，不回完整矩陣**：第三方之間是否結盟，不是觀測者必然知道的事。
  這樣既夠畫符號，又不順手洩漏他方政治關係。全局視角（全知未指定 as_faction）→ `observer: null` +
  `relations: {}`，前端據此不套敵我著色（維持既有全局行為）。
- **前端不另立判敵規則**：`relationOf` 未宣告一律回 `HOSTILE`，與後端 `FactionRelations` 的
  §12.1 預設同語義。contact 未達 IDENTIFIED 時 faction 未揭露 → 同樣保守標敵。
- **友軍不可被鎖為交戰目標**：`engageTargets` 與地圖點選/右鍵選單 5 處由 `!== observerFaction`
  改為 `!isFriendly(...)`，避免「盟軍出現在我方清單裡卻還能被點成打擊目標」。
- **盟軍只吃 ALLIED，不含 NEUTRAL**：中立方要靠偵測看見（走 `/intel`），不共享視圖。

## 測試證據
- 新增 5 條 API 測試（觀測者中心、全局無觀測者、一般角色越權 403、盟軍納入 units、
  **未宣告關係的局維持只見己方**——釘住既有局零行為變更）。
- gates：`pytest` **1084 passed / 8 skipped**（golden 6 未破）、ruff/mypy(204)/schema-sync、
  前端 lint + typecheck 全綠。
- **瀏覽器實測（複製局宣告 BLUE↔YELLOW=ALLIED，驗完刪除）**，BLUE 視角下地圖圖徵：

  | 圖徵 | 數量 | affiliation |
  |---|---|---|
  | `own/BLUE` | 13 | **F**（Friendly） |
  | `own/YELLOW` | 10 | **F**（Friendly）← 盟軍 |
  | `contact/RED` | 13 | **H**（Enemy） |

  單位面板同步顯示「單位（23）＝ BLUE 13 + YELLOW 10」（盟軍納入共享視圖）。
  `/relations?as_faction=BLUE` 回 `{BLUE: ALLIED, RED: HOSTILE, YELLOW: ALLIED}`。

## 未做
- **白軍局中宣戰/停火的 UI**：`set_relation` 已能產 `FACTION_RELATION_CHANGED` 事件，但沒有入口；
  目前只能由想定宣告或直接改 DB。
- 想定編輯器已可編關係矩陣（O7.3），但**既有想定都沒宣告** → 需在想定裡設定才看得到友軍效果。
- 陣營視角下仍無法下 ENGAGE 令（#90 記錄的限制，需 contact→unit 解析，另案）。

## 中斷續作指引
- **下一步第一件事**：#92 地圖標註陣營歸屬與視角過濾（後端 `ownerFaction` 與可見性過濾都已具備，
  缺前端顯示歸屬 + 全知在某視角下繪製時歸該陣營 + list 帶 as_faction）。
