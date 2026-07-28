---
task: "#92 地圖標註陣營歸屬與視角過濾"
status: DONE
started: 2026-07-28T17:20+08:00
updated: 2026-07-28T17:55+08:00
agent: Opus 5
spec: SPEC_FULL §13.3（fog of war）、§12（RBAC）
---

# #92 標註歸誰、誰看得到

## 盤點：後端本來就大致齊了
- `MapFeature.ownerFaction` 欄位存在；
- `map_features.py` 已依「全知全見／否則共同(WHITE_CELL)+本軍」過濾；
- 建立/編修權限已依陣營控管（一般角色冒用他軍 → 403）。

**缺的三件**：list 不吃 `as_faction`（白軍切了視角仍看到全部）、前端不顯示歸屬、
全知繪製一律落 `WHITE_CELL`（等於白軍替某軍畫的東西全體都看得到）。

## 交付
| 檔案 | 動作 | 說明 |
|---|---|---|
| `contracts/core_api.yaml` | 修改 | `listMapFeatures` +`as_faction` |
| `core/app/api/map_features.py` | 修改 | 視角過濾（僅全知可指定，一般角色→403，與 units/intel/relations 同紀律） |
| `platform/app/composables/useMapFeatures.ts` | 修改 | `fetchMapFeatures(sessionId, asFaction)` |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | 載入帶視角；**繪製時 `owner_faction = viewpoint`**；標註列加歸屬徽章 |

## 設計決定
- **繪製歸屬跟著視角走**：全知在 BLUE 視角畫的東西歸 BLUE，而不是落共同層。
  否則白軍替某軍標記的東西會全體可見，等於繞過 fog。未切視角時仍為共同層（沿用舊行為）。
- **徽章分兩態**：`WHITE_CELL` → 「共同」；否則陣營色點 + 陣營代號，tooltip 說明誰看得到。
  讓「這是誰畫的、誰看得到」在清單上一眼可辨，而不是要點開才知道。
- **過濾仍只在後端**：前端只是把 viewpoint 當參數帶上去（紅線 #3）。

## 測試證據
- 新增 2 條測試：視角過濾（含**與該陣營帳號登入所見一致**的交叉驗證）、一般角色越權 403。
- gates：`pytest` **1086 passed / 8 skipped**（golden 6 未破）、ruff/mypy(204)/schema-sync/buf、
  前端 lint + typecheck 全綠。
- **API 實測**（複製局，建 COMMON/BLUE-OP/RED-OP 三個標註）：

  | 視角 | 看到 |
  |---|---|
  | 全局 | COMMON、BLUE-OP、RED-OP（+ 既有共同標註） |
  | BLUE | COMMON、BLUE-OP（**無 RED-OP**） |
  | RED | COMMON、RED-OP（**無 BLUE-OP**） |

- **瀏覽器實測**：標註列徽章顯示 `RED-OP → RED`、`BLUE-OP → BLUE`、`COMMON → 共同`；
  切視角後清單同步收斂（全局 3 → BLUE 2 → RED 2）。測試局已刪、DB 仍 3 局。

## 未做
- 既有標註的歸屬**無法在 UI 改**（PATCH 支援欄位，但編輯面板沒放這個欄位）；
  目前要改歸屬得重畫或直接改 DB。
- 地圖上的標註圖形本身未依歸屬著色（清單有徽章，圖形仍用 `attributes.color`）。
