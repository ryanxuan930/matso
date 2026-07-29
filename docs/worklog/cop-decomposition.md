---
task: "WP-G1a cop.vue 拆分：狀態與面板層（前端工程健全化）"
status: DONE
started: 2026-07-30T01:40+08:00
updated: 2026-07-30T09:10+08:00
agent: Opus 5
spec: SPEC_V2.md §WP-G 表 G1；README §8 前端債盤點
---

# WP-G1a cop.vue 拆分：狀態與面板層

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
| 檔案 | 動作 |
|------|------|
| `composables/useCopWidgets.ts` / `useCopPrefs.ts` / `useWeaponTracks.ts` / `useUnitCardDrag.ts` / `useCopOrdering.ts` / `useMapEditor.ts` | **新增**（六個 composable） |
| `components/cop/MapEditorPanel.vue` / `UnitsOrderPanel.vue` / `UnitDetailCard.vue` | **新增**（三個面板元件） |
| `pages/session/[id]/cop.vue` | 4419 → 2181 |
| `components/map/MapCanvas.vue` | 修 4 個型別錯（見下） |
| `platform/package.json` | `typecheck` 由空轉的 `vue-tsc --noEmit` 改為 `vue-tsc --build` |
| `contracts/core_api.yaml` | `OrderRequest.payload` 補 `additionalProperties`；`acknowledge_restricted` 拿掉 `default` |

## 測試證據
- `npm run lint` / `npm run typecheck`（**現在真的會檢查**）/ `npm run build` 全綠。
- e2e：`.env`-free worktree 內基準線與 G1a **各 4 failed / 14 passed，失敗清單完全相同**。
- 容器 + 瀏覽器逐步實測（每個 commit 一次），詳見下方進度表的「驗證」欄。
- 多 agent 等價性稽核：確認 5 個回歸並全數修掉（見「收尾」節）。

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
| `UnitDetailCard.vue` | 2181 | 卡片渲染且**樣式跟著到位**（position fixed / 304px / z-index 45）、效能條、編裝編輯入口 |

目前組成：script 864 / template 549 / style 745。

### 第三個發現：搬 CSS 這步 lint 與 typecheck 都無感，**只有 build 會擋**
`UnitDetailCard` 第一版把 CSS 區段多切了兩行（`.map-loading {` 的開頭），
lint 綠、typecheck 綠，`npm run build` 直接 `CssSyntaxError: Missing closing }`。
故凡是搬 scoped CSS 的步驟，驗證順序是 **lint → typecheck → build（容器重建即含）→ 瀏覽器**。

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

## 剩餘拆分的測繪（唯讀盤點，於等待稽核 workflow 時做）
以目前的 cop.vue（2181 行；script 864 / template 549 / style 745）為準：

| 產出 | 樣板行段 | scoped CSS 行段 | 估計減少 |
|------|---------|----------------|---------|
| `CopHeader`（頂列：session/單位數/通聯姿態/時鐘/視角/導覽鈕/小工具選單） | 873–995 | `.cop-bar` 1704–、`.sid/.count/.posture` 1814–1830、`.cop-nav*` 1831–1932、`.vp*` 1846–1872、`.widget-menu/.wm-*` 1715–1759 | ~120 + ~230 |
| `EquipManagerPanel`（＋`useEquipMgr`） | 1034–1130 | `.equip-overlay`–`.eq-hint` 1570–1693 | ~100 + ~124 |
| `MapContextMenu` | 1333–1400 | `.ctx-backdrop`–`.ctx-empty` 1976–2023 | ~68 + ~48 |
| `MapStateEditBar`（＋`useMapStateEdit`） | 996–1010 | `.mapedit-bar*` 1522–1569 | ~15 + ~48 |
| `LayersPanel` | 1280–1318 | `.linewidth-btn` 1958–1975、`.modal*` 2024–2085 | ~38 + ~80 |
| `OrdersPanel` / `EventsPanel` | 1136–1198 | `.orders*` 2094–2118、`.empty` 2106 | ~62 + ~25 |
| `CoordReadout` | 1404–1431 | `.coord-readout*` 2135–2172 | ~28 + ~38 |
| 地圖區包裝（MapCanvas 呼叫點 + notice + loading） | 1200–1278 | `.map-wrap*` 2119–2134、`.map-notice` 1933–1957、`.map-loading` 2173– | ~78 + ~55 |

**對 < 800 的誠實推估**：以上全做完約再減 1100 行 → cop.vue 落在 **1050–1150**。
要真的到 800 以下，還得把 `body` 版面容器與 Teleport/停靠邏輯本身也包成一層
（`CopWorkspace`），並把 script 尾巴的 `useCtxMenu` / `useEquipMgr` / `useMapStateEdit` 搬走。
**做得到，但這張卡的體量遠大於卡片描述**——它實質上是把一個 4400 行單體重寫成一組元件。
若要在單一 session 內收斂，比較務實的做法是把「< 800」改成分兩張卡：
G1a（composables + 面板元件，已完成大半）與 G1b（版面/頂列/地圖區元件化）。

## 待辦（依序）
- [x] `MapEditorPanel`（template 236 行 + CSS 314 行）
- [x] `UnitsOrderPanel`（template 204 行 + CSS 318 行；`liveAmmo` 一併併入 `useCopOrdering`）
- [x] `UnitDetailCard`（template 82 行 + CSS 158 行）
- [x] 修掉稽核確認的 5 個回歸
- [x] e2e 與拆分前對等比較（`.env`-free worktree）
- [x] 更新 SPEC_V2（G1 拆成 G1a/G1b）/ PROGRESS / TASKS
- → 以下移交 **G1b**：`LayersPanel`、events/orders/coords、`CopHeader`、地圖區包裝、
  `useMapStateEdit`/`useEquipMgr`/`useCtxMenu`、MapCanvas props 收斂

## 收尾（G1a 完成）

**使用者裁示（2026-07-30）**：G1 拆成 G1a（狀態與面板層，本卡）與 G1b（版面層，收到 < 800 行）。
理由見上方推估——這張卡的實質工作是把 4400 行單體重寫成一組元件，一次收完不利驗證。
G1a 已達成這張卡真正的目的：後續前端卡不必再讀 4400 行才敢動手。

### 多 agent 稽核（ultracode workflow）——**抓到 5 個我自己沒看見的回歸**
8 位審查者逐塊比對 `git show d5c585a` 的拆分前原始碼，每項指控再派一位**專門反駁**的
審查者查證，另一位做漏網掃描。結果：**稽核與掃描各自獨立指向同一組 5 個缺陷**，
對抗式階段一個都沒能反駁掉。全部出在 `UnitsOrderPanel` 那一步的 CSS 切分：

| # | 缺陷 | 症狀 |
|---|------|------|
| 1 | `class="precheck"` / `data-testid="precheck"` 被字串取代波及成 `ordering.precheck` | 預檢綠/紅配色失效；**e2e 三處 `getByTestId('precheck')` 會找不到元素** |
| 2 | `.ord-*` 整組搬進子元件變死規則 | 「指令」小工具失去雙層 flex、狀態徽章配色 |
| 3 | `.events` / `.events li` / `.ws` 同上 | 「戰況事件」失去琥珀左邊條與深色底 |
| 4 | 父層的 `.orders`/`.orders li`/`.empty` 是**憑印象重寫**而非逐字照抄 | 邊框、內距、`cursor` 覆寫被靜默改掉 |
| 5 | `.unit-card .lowfuel` 落在沒有 `.unit-card` 的元件 | 油料歸零不再轉紅 |

第 4 項是最該記住的：我在自己訂的「只搬不改」紀律下，仍然手抄了規則。
**搬 CSS 一律 `git show <前一版>` 取原文，不得憑印象重寫。**

### e2e：與拆分前逐條相同
`platform/.env`（含 `NUXT_PUBLIC_TILE_URL`）會讓「離線：無 tile server」那條測試在本機必紅，
**與程式碼無關**。故改在 `.env`-free 的 worktree 內同時跑基準線與 G1a：

| | 結果 | 失敗清單 |
|---|------|---------|
| 拆分前 `d5c585a` | 4 failed / 14 passed | map:71 地圖縮放平移、orders:24、orders:47、smoke:11 |
| G1a `7806d50` | 4 failed / 14 passed | **完全相同的四條** |

即**重構帶來零 e2e 變化**。那四條在拆分前就是紅的（記入 PROGRESS backlog，屬 G3）。

## G1b 進度（進行中）

| 步驟 | commit | cop.vue |
|------|--------|---------|
| G1a 收尾 | `7806d50` | 2181 |
| `CopHeader` | `701fd61` | 1969 |
| `EquipManagerPanel` | `4f298a3` | 1800 |
| `MapContextMenu` + 右鍵 e2e | `e0bbff1` | 1728 |
| `CopWidget` 外殼（六個小工具共用） | `2951d6a` | 1631 |
| `LayersPanel`/`EventsPanel`/`OrdersPanel`/`MapStateEditBar` + `useCopFeed` + 清死 CSS | `7b0fff8` | 1281 |
| `useMapStateEdit` / `useEquipMgr` | `311ac4d` | 1200 |

### 這一段學到的三件事

**1. 先抽共用外殼，再抽內容。** 六個小工具各自寫了 18 行一模一樣的
`Teleport` + `FloatingWidget` 綁定。若先做 `LayersPanel` 等內容元件，這 108 行重複
只會被搬進六個新檔案。抽 `CopWidget` 後，內容元件才變成真正只有內容。

**2. 兩個名字很像、語義相反的東西，沒有任何工具會擋。**
`canEditSelectedFeature`（已解鎖整形）vs `mayEditSelectedFeature`（有編修權）——
批次取代差點把兩者併成同一個 prop。「有權 ≠ 現在可拖」是 #99b 刻意的設計
（單純點選不解鎖，否則點一下再手滑就把標註拖歪）。lint/typecheck/build 全綠。

**3. CSS 特異度平手時，靠先後決勝負。**
`.ord-meta button`（margin-left: auto）與 `.orders button`（margin-left: 0.5rem）
特異度相同，原檔中後者在後 → 後者贏。搬移時若重排順序，取消鈕的位置與顏色都會變。
驗證方式不是用眼睛看，是**逐條讀 computed style 與原始值對表**。

## 中斷續作指引
- **G1b 進行中**，cop.vue 已 4419 → 1200。剩下：`CoordReadout`、地圖區包裝、
  `useCtxMenu`（script 498–604，約 107 行，是最後一塊大的）、
  MapCanvas props 收斂（50 個 prop → 分組設定物件）。
- **下一步第一件事**：`useCtxMenu`。它是右鍵選單的狀態機（ctxMenu ref + 8 個 ctx* 動作
  + 3 個 computed），與下令狀態機、`useMapEditor`、`selectUnit` 都有牽連——
  **抽之前先把相依清單列出來**，若參數超過 6 個就代表切點選錯了，改切小一點。
- `CoordReadout` 的 CSS 有陷阱：`.coord-readout` 基底規則被 cop.vue 的
  `:deep(.fw .coord-readout)` 整條中和（position/border/background/padding 全歸零）。
  搬走時基底規則進子元件、`:deep` 中和留在 cop.vue——特異度 (0,3,0) > (0,2,0)，
  跨檔順序不影響結果，但要**實測**而不是推論。
- **四道驗證照舊**：`lint → typecheck → build → 瀏覽器`。三者各有盲區：
  typecheck 抓不到元件名解析失敗、build 才抓得到 scoped CSS 切壞、
  瀏覽器才看得出面板整個是空的。
- **搬 CSS 一律 `git show` 取原文**，含規則先後順序。
- **scoped CSS 的必要重複**：`.orders, .events {…}` 這種共用規則拆到兩個元件時，
  兩邊各留一份（已於 `7b0fff8` 處理）。
- **收卡前**：跑一次等價性稽核 workflow（腳本可重用：
  `.claude/.../workflows/scripts/g1-refactor-equivalence-audit-*.js`），
  再於 `.env`-free worktree 做 e2e 前後比對，然後更新 SPEC_V2 / PROGRESS / TASKS。
- **未解**：e2e 只有 6 支（本段新增 `ctxmenu.spec.ts`），仍未涵蓋 COP 的下令流程與
  地圖編輯；`toggleOrbatPerm` 因會實際改動該局自編權限設定而未手測。
