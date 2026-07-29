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

## 檔案異動
（施工中）

## 測試證據
（施工中）

## 中斷續作指引
- **下一步第一件事**：抽 `useCopWidgets` + `useCopPrefs`（兩者共用同一把 localStorage 鑰匙，
  必須一起搬才不會破壞持久化格式）。
