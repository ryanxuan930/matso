---
task: "WP-G1 cop.vue 拆分（前端工程健全化第一張）"
status: IN_PROGRESS
started: 2026-07-30T01:40+08:00
updated: 2026-07-30T01:40+08:00
agent: Opus 5
spec: SPEC_V2.md §WP-G 表 G1；README §8 前端債盤點
---

# WP-G1 cop.vue 拆分

## 目標摘要
`cop.vue` 4419 行單體（script 1849 / template 1067 / style 1498）。以既有區塊邊界抽出
composables 與子元件，**行為零變更**，目標 < 800 行。這是後續所有前端卡（G4 白軍控制台成熟化、
D6.1 AAR 地圖重播）的前置——在 4400 行的檔案裡加功能，每次都要重讀整份才敢動。

## 風險與對策（開工前先講清楚）
**驗收條文寫「既有 e2e 全綠」，但 e2e 只有 5 支**（auth/smoke/map/units/orders），
而 G3 那張卡的存在本身就說明覆蓋不足。純靠 e2e 當安全網會漏。故本卡的紀律是：
1. **每次只搬一塊，搬完立刻 `npm run lint` + `vue-tsc`**——TypeScript 是這裡最有效的網子
   （props/emit 型別不符會當場紅），比事後跑一次全套有用。
2. **只搬不改**：搬動時不順手重構邏輯、不改名、不「順便修」。任何看起來像 bug 的東西
   記進 PROGRESS backlog，不在本卡動（紅線 5）。
3. 每個綠燈點 commit，讓任何一步出錯都能二分定位。
4. 最後以容器 + 瀏覽器逐項手測（清單見下）。

## 拆分計畫
| 產出 | 來源行段 | 內容 |
|------|---------|------|
| `composables/useCopWidgets.ts` | 104–192 | 浮動工具視窗：WStat/停靠/z 序/開關 |
| `composables/useCopPrefs.ts` | 76–103、193–210、1729–1849 | 圖層/底圖/網格偏好 + `preciseMove` + localStorage 持久化（**同一把鑰匙、同一個 JSON 形狀**） |
| `composables/useWeaponTracks.ts` | 497–540、601–637 | #95 武器軌跡（純顯示） |
| `composables/useUnitCardDrag.ts` | 590–707 | 資訊卡錨定 + 拖曳 |
| `composables/useCopOrdering.ts` | 291–346、1469–1640 | 下令狀態機、移動預覽、送出/取消 |
| `composables/useMapEditor.ts` | 249–290、1049–1440 | 標註/工事繪製、編輯、整形、地形裁切 |
| `composables/useMapStateEdit.ts` | 982–1047 | 白軍地圖狀態編輯（暫停下拖放） |
| `composables/useEquipMgr.ts` | 942–977 | COP 裝備管理面板 |
| 子元件（template + 對應 scoped CSS 一起搬） | 1852–2919 / 2921–4419 | UnitCard / OrderPanel / MapEditorPanel / LayersPanel / 其餘小工具 |
| MapCanvas props 收斂 | — | 50 個 props → 分組 config 物件 |

## 手測清單（最後在容器內逐項確認）
- [ ] 圖層開關/透明度/順序/等高線間距 → 重整後保留
- [ ] 六個浮動視窗：拖曳、停靠左右、縮放、關閉、選單開關 → 重整後保留
- [ ] 選單位 → 資訊卡出現、可拖曳、關閉
- [ ] MOVE：選點、精確/六角、自訂路徑、預覽（距離/tick/油耗/阻礙）、送出、取消
- [ ] ENGAGE：右鍵鎖定、武器/彈種、火力政策、送出
- [ ] 地圖編輯：繪點/線/面/圓/矩形、選取、編輯屬性、整形、旋轉、刪頂點、刪除
- [ ] 地形裁切環套用/清除
- [ ] 白軍：視角切換、地圖狀態編輯（拖放單位、多選整組移動）、開始兵推
- [ ] 裝備管理面板、編裝自編權限
- [ ] 戰況事件流、指令列表、勝負橫幅、AI 狀態列

## 執行紀錄
- `01:40` 開卡；讀完 cop.vue 全域宣告清單，定出上表的搬移邊界。
- `02:10` `useCopWidgets` + `useCopPrefs` 完成（4419→4207）。
- `02:30` `useWeaponTracks` + `useUnitCardDrag` 完成（4207→4055）。
- `02:55` **抽 `useCopOrdering` 時驗證「TypeScript 是安全網」這個假設，發現它是假的**——見下。
- `03:20` 修好 typecheck 閘門並清掉它藏起來的 6 個型別錯；`useCopOrdering` 完成（4055→3871）。
- `03:40` 容器實測：COP 正常渲染、選單位 → 資訊卡出現且內容正確、
  `matso.cop.layers` 的 25 個欄位與 6 個 widget 幾何**與搬移前完全相同**。

## ⚠ 最重要的發現：`npm run typecheck` 一直是空轉

開卡時我把「每搬一塊就跑 lint + vue-tsc」寫成本卡的主要紀律。抽 `useCopOrdering` 到一半時
順手驗證這個假設——在 cop.vue 塞一行 `const x: number = 'str'`，**typecheck 照樣綠**。

原因：`platform/tsconfig.json` 是 `"files": []` ＋ 四個 project references，而
`vue-tsc --noEmit` **不會跟隨 references**。所以 `app/` 底下的 `.vue` 與 `.ts`
**從來沒有被 type check 過**——近期每一份 worklog 裡的「vue-tsc 綠」對前端而言都是空話
（包含我自己在 WP-C5 寫的那句）。eslint 有在跑，但它只抓得到未使用變數那類問題。

改成 `vue-tsc --build` 後跑出 6 個錯，全部清掉：

| 位置 | 問題 | 處置 |
|------|------|------|
| MapCanvas ×3 | `queryRenderedFeatures(e.point, …)`：handler 的 `e` 是**手寫結構型別**，`point` 是 `{x,y}` 而非 maplibre 的 `Point` 類別 | 改傳 `[x, y]`（合法 `PointLike`），語義相同且不需 cast |
| MapCanvas ×1 | `watch([...], syncUnits)` 會把「新值陣列」當第一個參數傳給 `syncUnits`，剛好落在 `posOverride`。陣列沒有 `.size` 所以**僥倖**沒事 | 包成 `() => syncUnits()` |
| 契約 | `OrderRequest.payload` 是裸 `type: object` → openapi-typescript 產 `Record<string, never>`＝任何具體 payload 都不可指派 | 補 `additionalProperties: true`（描述本來就寫「依 order_type 而異」） |
| 契約 | `acknowledge_restricted` 的 `default: false` 讓產生器把它標成**必填** | 拿掉 default，語義寫進描述 |

**這件事改變了本卡的風險評估**：先前三個 commit 的「typecheck 綠」是無效證據，其中確實藏了
兩個真的錯（`unitCardDrag` 沒 export、`liveFuel`/`liveAmmo` 被誤刪），都是修好閘門後才抓到的。
往後每一步都以 `vue-tsc --build` 為準。

## 檔案異動
（施工中）

## 測試證據
（施工中）

## 進度（cop.vue 行數）
| 步驟 | 行數 | 驗證 |
|------|------|------|
| 開卡 | 4419 | — |
| `useCopWidgets` + `useCopPrefs` | 4207 | 容器實測 `matso.cop.layers` 的 25 欄 + 6 個 widget 幾何未變 |
| `useWeaponTracks` + `useUnitCardDrag` | 4055 | — |
| **修 typecheck 閘門** + `useCopOrdering` | 3871 | 選單位 → 資訊卡/通聯/座標/彈藥皆正確 |
| `useMapEditor` | 3483 | 點標註列 → 編輯欄位載入「樓梯」；繪製工具列六個鈕齊全 |
| `MapEditorPanel.vue`（首個子元件） | 2907 | 面板渲染 9 列標註、v-model 寫入生效、點列載入「樓梯」、`canControl` 分支出現 |
| `UnitsOrderPanel.vue` | 2406 | 3 個陣營分組 / 36 單位、點選 → B3、精確移動勾選狀態沿用偏好、切 ENGAGE 出現目標與武器下拉 |

目前組成：script 872 / template 634 / style 894。

### 子元件那步踩到的兩件事

1. **`vue-tsc` 抓不到「元件名解析不到」**。我先把它命名為 `<CopMapEditorPanel>`（Nuxt 預設
   `pathPrefix: true` 的名字），typecheck 與 lint **雙綠**，但畫面上那個小工具是空的——
   Vue 樣板把不認識的標籤當原生元素處理，不報型別錯。本專案設定是
   `components: [{ path: '~/components', pathPrefix: false }]`（HOW_TO §3.2：依檔名匯入），
   正確名字是 `<MapEditorPanel>`。**教訓：子元件這步一定要進容器看，typecheck 綠不代表有渲染。**
2. **樣板不會 unwrap 巢狀 ref**。`:editor="mapEditor"` 直接傳 composable 回傳值的話，
   `editor.drawLabel` 拿到的是 Ref 物件本身，v-model 綁上去就壞了（typecheck 這次有抓到，
   一次噴 20 個錯）。解法是傳 `reactive(mapEditor)`——遞迴 unwrap 且寫入回寫原 ref，
   父子共用同一份狀態。子元件的 prop 型別因此是 `UnwrapNestedRefs<ReturnType<typeof useMapEditor>>`。
   另外 eslint 的 `vue/no-mutating-props` 會擋——已在該元件加**具理由的**局部 disable：
   這個 prop 是共享可變狀態束（等同把 store 當 prop 傳），沒有「第二份真相」可分岔。

## 下一步的設計決定：子元件收「composable 束」而非 40 個 props

地圖編輯面板要綁 `drawKind`/`drawFeatureKind`/`drawLabel`/`drawColor`/… 二十幾個
v-model，逐個開 prop + emit 就是把 MapCanvas 那個「50 個 props」的毛病複製一份
（那正是本卡要修的東西之一）。

改法：**`useMapEditor()` 的回傳物件整包當一個 prop 傳下去**
（`<CopMapEditorPanel :editor="mapEditor" />`）。ref 傳下去仍是 ref，
子元件內 `editor.drawLabel` 直接 v-model 可用，型別也完整（composable 的回傳型別即契約）。
代價是子元件與該 composable 綁定——但它們本來就是同一件事的兩半，這個耦合是誠實的。

同一招套用到 MapCanvas 的 props 收斂：依用途分組成
`layers` / `overlays` / `interaction` 三個 config 物件。

## 待辦（依序）
- [x] `MapEditorPanel`（template 236 行 + CSS 314 行）
- [x] `UnitsOrderPanel`（template 204 行 + CSS 318 行；`liveAmmo` 一併併入 `useCopOrdering`）
- [ ] `CopUnitCard`、`CopLayersPanel`、其餘小工具（events/orders/coords）
- [ ] `useMapStateEdit` / `useEquipMgr` / `useCtxMenu`（script 尾巴）
- [ ] MapCanvas props 收斂
- [ ] 容器逐項手測（清單見上）→ 更新 SPEC_V2 / PROGRESS / TASKS

## 中斷續作指引
- **下一步第一件事**：`UnitCard`（單位詳細資訊卡，template + CSS 都不小），
  照同一套做法。之後是 `LayersPanel` 與 events/orders/coords 三個小工具。
- **scoped CSS 的必要重複**：`.units, .orders {…}` 這種共用規則搬走一半時，
  另一半要在父層留一份（已於 `UnitsOrderPanel` 那步處理，父層留 `.orders`/`.empty`）。
- **紀律**：每搬一塊立刻 `npm run typecheck`（現在是 `vue-tsc --build`，真的會檢查）+ `npm run lint`，
  綠了才 commit。`npm run typecheck` 在本卡之前是空轉的，別再相信舊 worklog 的「typecheck 綠」。
- **未解**：e2e 只有 5 支且沒有涵蓋 COP 的下令/地圖編輯流程，本卡全程靠 typecheck + 容器手測。
