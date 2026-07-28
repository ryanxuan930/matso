---
task: "#99 地圖物件整形（控制點編輯）"
status: DONE
started: 2026-07-28T21:10+08:00
updated: 2026-07-29T00:20+08:00
agent: Opus 5
spec: TASKS.md #99（COP 視角／符號／設定／顯示 表格末列）
---

# #99 地圖物件整形：控制點拖曳改形狀 / 位置

## 目標摘要
地圖編輯器畫出來的點/線/面，原本**只能整點拖曳（點）或繞質心旋轉（線/面）**——
線/面畫錯一個頂點就得刪掉重畫。本卡補上其他地圖服務都有的整形能力：
選取圖形 → 顯示控制點 → 拖頂點改形狀、拖中點插頂點、拖本體整體平移、右鍵控制點刪點。

## 動工前盤點（避免重工／找出真正缺口）
- 後端 `PATCH /api/v1/sessions/{id}/map-features/{fid}` **早已接受 `geometry`**（`MapFeatureEdit.geometry: Any`），
  權限檢查 `_feature_for_edit` 也已完備 → **零後端改動、零契約改動、零 migration**。
- 已有的相鄰能力：`onFeatureMove`（點特徵整點拖曳，#11 B2）、`rotateFeature`（#26 繞質心旋轉）。
  兩者都已建立「幾何一變就清掉 `attributes.viewshed_ring`」的紀律（#43）——整形沿用，否則
  地形裁切環會停留在舊形狀上。
- 缺口只在 MapCanvas：沒有控制點圖層、沒有頂點拖曳、線/面本體不可拖。

## 交付
| 檔案 | 動作 | 說明 |
|------|------|------|
| `platform/app/composables/useMapFeatures.ts` | 修改 | 幾何純函數：`openRing`/`insertVertex`/`moveVertex`/`removeVertex`/`translateRing`/`midpoints` + `MIN_VERTICES`。**索引數學集中於此**，MapCanvas 與 cop.vue 共用同一套語義 |
| `platform/app/components/map/MapCanvas.vue` | 修改 | `mapfeatverts` 來源 + `mapfeat-vertex`/`mapfeat-midpoint` 兩層；頂點/中點/本體三種拖曳；右鍵帶 `vertexIndex`；新 prop `featureEdit`、新 emit `featureReshape` |
| `platform/app/pages/session/[id]/cop.vue` | 修改 | `canEditSelectedFeature`（與後端同一條權限規則）、`onFeatureReshape`（PATCH＋失效裁切環＋失敗重載）、`ctxDeleteVertex`（下限檢查＋提示）、操作說明列 |

## 三個設計決定（與為什麼）
1. **新幾何由 MapCanvas 算完再 emit**，不是 emit「第幾個頂點動到哪」。
   插點/刪點的索引語義只在一處（拖曳發生的地方）定義，上層只負責存。
2. **控制點優先於本體**（見「踩到的坑」）：用**命中查詢**判定，不靠監聽器註冊順序——
   順序是隱性契約，日後調換註冊位置就會再度失效。
3. **拖曳中只做本地預覽，放開才 PATCH**；且**PATCH 失敗一律 `loadFeatures()`**——
   畫面上當下是拖過的形狀，不重載就會停在一個伺服器沒接受的幾何上。

## 踩到的坑（都是實測才發現的）
- **拖頂點變成整條平移**：控制點畫在線/面之上，同一次 mousedown 會同時觸發
  `mapfeat-vertex` 與 `mapfeat-line` 兩個委派監聽器，後跑的覆蓋前者。
  實測證據：拖中間頂點後三個頂點同時位移相同量。→ `onBodyDown` 先查有無控制點命中，有就讓路。
- **首尾重合的環**：`genCircle`（圓/矩草稿）存放環自己就首尾重合，`featuresToFc` 又補一個閉合點，
  於是 FC 環尾端有**兩個**重合點。不去重的話同一位置疊兩顆控制點，拖走一顆另一顆留著＝圖形「裂開」。
  → `openRing` 一路剝掉與首點重合的尾點。實測：存 5 點的方形環 → 控制點 4 顆、4 個相異位置。
- **放手放在小工具視窗上**：`map.once('mouseup')` 只收得到 canvas 容器上的放開，而浮動小工具就疊在
  地圖上。→ 加 window 級 fallback（`armReshapeFallback`），`endReshape` 本身重入安全，
  正常情況兩者都觸發也只 emit 一次。

## 順手修好的既有 bug（同一功能區、使用者這次就是在講「移動物件」）
**點特徵一直拖不動**（#11 B2 自始無效）：
```js
const featLayers = () => ['mapfeat-point','mapfeat-symbol'].filter((l) => map?.getLayer(l))
for (const l of featLayers()) map.on('mousedown', l, onFeatDown)
```
這段在 `onMounted` 同步跑，但圖層是 `map.on('load')` 裡才加的 → 註冊當下 `getLayer` 一律 undefined
→ 過濾成空陣列 → **這兩層的 mousedown 從來沒被註冊過**。
實測證據：`map._delegatedListeners.mousedown` 只有 units/fill/line，沒有 mapfeat-point/symbol。
maplibre 的委派監聽器是**事件發生時**才查圖層，對尚未存在的圖層註冊是安全的（本卡新加的控制點
監聽器就是這樣註冊而運作正常）→ 移除該過濾即修復。修好後實測拖曳點特徵，座標確實落在放手處。

## 測試證據（皆為容器內實測，並以 GET 回讀伺服器權威幾何，非只看前端預覽）
於使用者既有 session 建**臨時測試圖徵**操作，驗完全數刪除（該局標註回到原本 8 筆，未動到既有資料）。

| 測項 | 結果 |
|---|---|
| 選取線 → 控制點 | 3 頂點 + 2 中點；操作說明列出現 |
| 拖頂點 | **只有 index 1 變動**，端點不動；伺服器回讀＝放手處 |
| 拖中點 | 3 → 4 點，新點**插在 index 0 與 1 之間**（非附加到尾端）；中點重算為 3 |
| 拖本體 | 4 點**同位移**（Δ 一致到小數 7 位）、點數不變 |
| 右鍵控制點 | 選單標題「控制點 #1/#2/#3」與頂點序一致（無 off-by-one） |
| 刪控制點 | 4 → 3 且刪到的正是該點 |
| 下限保護 | 3 → 2 可刪；2 再刪被拒，toast「線至少需要 2 個控制點」，伺服器仍 2 點 |
| 首尾重合的面 | 存 5 點 → 控制點 4 顆／4 個相異位置、中點 4 顆（含末→首那段）；拖頂點後存回**正規化的 4 點開放環** |
| 點特徵拖曳（修復驗證） | 委派監聽器已含 mapfeat-point/symbol；拖曳後伺服器座標＝放手處 |

- `cd platform && npm run lint` → 綠；`npm run typecheck`（vue-tsc）→ 綠。
- 後端未改 → 未跑 pytest/golden（本卡零 Python 變更）。

## 驗證方法的注意事項（下一個要在這個環境測地圖的人請先讀）
- 自動化面板的 `computer` 點擊**進不到 maplibre canvas**（DOM 按鈕可以）。地圖互動要用
  `dispatchEvent` 打真實事件序列。
- maplibre 的 map-`click` 來自 **DOM `click`**（瀏覽器只在真實輸入序列後合成），
  純 dispatch mousedown/mouseup **不會**產生 → 要自己補一發 `click`。
- `mouseup` 必須派到 **canvas 容器**；派到 window 收不到 `map.once('mouseup')`。
- 事件座標要加上容器的 `getBoundingClientRect().y`（本專案地圖上方有 ~46px 標題列），
  否則整批點擊都差一個標題列高度。
- 傳給 `map.fire('contextmenu', {point})` 的 point **必須是 Point 實例**（用 `map.project()` 取），
  傳 `{x,y}` 字面物件會讓 `queryRenderedFeatures` 走成 bbox 分支，查出來的索引是錯的
  （我一度誤判成 off-by-one）。
- 面板背景時 `document.hidden=true` → rAF 停 → 畫面不重繪，`queryRenderedFeatures` 一律空。
  `map._render(0)` 可強制同步繪一幀再查。

## 未做 / 後續
- **圓形不再是圓**：圓存成 POLYGON，拖頂點就成了任意多邊形（與 Google 編輯多邊形同行為）。
  若要「拖控制點只改半徑」需另存圓心+半徑的參數化幾何——非本卡範圍。
- 頂點多於 24 顆時**不畫中點**（圓形有 48 頂點，畫了會糊成一片），故密集圖形不能用中點插點。
- 後端 `geometry` 仍是 `Any`、不驗證形狀（前端擋住下限，但直接打 API 仍可存出退化幾何）。
  記入 PROGRESS backlog。

## 追加（#99b，使用者回報）：整形要先解鎖，避免誤觸
**問題**：選取即出現控制點 → 在地圖上點一下再手滑，就把標註拖歪了；而點選是最頻繁的操作。

**做法**：把「有編修權」與「現在可拖」拆成兩件事。
- `mayEditSelectedFeature`＝權限（與後端 `_feature_for_edit` 同一條規則）。
- `reshapeArmedId`＝**明確解鎖的那一個** id；`canEditSelectedFeature = 有權 ∧ 已解鎖`，
  控制點與所有拖曳（含點特徵整點拖曳）都吃這個。
- 解鎖入口：右鍵選單「編輯形狀 / 屬性」，或編輯面板的「調整形狀」鈕。
- 自動上鎖：換選別的圖形、取消選取、開始繪製（`selectedFeatureId` 的 watch + `startDraw`）。
- 面板恆顯示目前狀態：鎖定時是灰色「形狀已鎖定（避免誤觸）」+ 解鎖鈕，
  解鎖時是藍色「調整中：…操作說明」+「完成」鈕——讓「為什麼拖不動」永遠有答案。

**實測**（於使用者既有標註，全程唯讀不改幾何）：
| 步驟 | 結果 |
|---|---|
| 左鍵選取線 | 控制點 0、顯示「形狀已鎖定」、屬性面板正常可編 |
| 右鍵 →「編輯形狀 / 屬性」 | 控制點 7 頂點 + 6 中點、切為「調整中」 |
| 換選另一條線 | 自動上鎖（控制點 0） |
| 面板「調整形狀」 | 解鎖（8 頂點 + 7 中點） |
| 面板「完成」 | 上鎖（控制點 0） |
| **鎖定時拖線本體** | **伺服器幾何逐字不變**（這條才是本次的重點） |

## 追加（#99c）：刪控制點補一條快捷路徑 + 兩個實測抓到的 bug
右鍵選單的「刪除控制點」在 #99 就做了，但只有那一條路徑；補上 **Alt＋點控制點＝刪點**
（`featureVertexDelete` emit → 上層 `deleteVertexAt` 與右鍵選單共用同一套下限檢查與提示）。

**順帶修掉兩個實測才現形的問題**：
1. **右鍵按在控制點上會被當成開始拖曳**：委派 layer 監聽器對任何鍵都觸發，
   於是「右鍵刪點」會先送出一筆無意義的幾何 PATCH；滑鼠在按下與放開之間動個一兩像素，
   還會真的把點挪走。→ 三個拖曳起點（頂點/本體/點特徵）一律只認 `button === 0`。
   實測：右鍵在控制點上拖 50px，伺服器幾何逐字不變。
2. **點控制點會把整個圖形取消選取**：控制點畫在線之上，但線只有兩三像素寬，
   命中測試常常「打在控制點上卻沒打到線」→ 落到 `mapClick` → 上層清掉選取
   → 控制點在按下去的瞬間整組消失（實測就是這樣把整形狀態弄丟的）。
   → map click handler 先查控制點圖層，命中就當作點在該圖形上。
3. Alt＋點**中點**原本會掉回插點流程（多一個點，與「Alt＝刪點」預期正好相反）→ 改為直接忽略。

**實測**（新建 5 點測試面，驗完刪除）：
| 操作 | 結果 |
|---|---|
| Alt＋中點 | 點數不變（5），且選取/解鎖狀態保住 |
| Alt＋頂點 ×2 | 5 → 4 → 3，每次都只刪掉該點 |
| Alt＋頂點（已 3 點） | 拒絕 + toast「面至少需要 3 個控制點」，伺服器仍 3 點 |
| 右鍵在控制點上拖 50px | 幾何逐字不變 |

## 追加：頂列導覽鈕改 icon-only
六顆導覽鈕（地圖狀態編輯／白軍控制台／裝備管理／工具／自主推演／AAR）只留 icon，
名稱與說明改為 hover 提示。**自繪提示而非原生 `title`**：原生的要等約 1 秒才出現，
icon-only 之下等於沒有提示。提示靠**按鈕右緣**往左長——導覽列本身靠右，置中對齊會讓最右邊
幾顆的提示被視窗切掉（實測 AAR 那顆會超出約 90px）。z-index 1002 壓過工具選單彈出層。

## 中斷續作指引
- 本卡已完成並驗證，無未竟項；後續想做的見「未做 / 後續」。
