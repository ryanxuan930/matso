# DataSearch — RAG 語料蒐集任務書（交付給資料蒐集 Agent）

> 這是一份**獨立任務書**。你（接手的 AI Agent）不需要本專案的對話脈絡即可執行。
> 目標：為 MATSO（AI 輔助兵棋推演系統）的 RAG 向量庫蒐集／整理／產出語料文件。
> 若你有本 repo 讀取權，請先讀 `ai/rag/corpus/README.md`（權威格式）與 `SPEC_FULL.md` §9.4、§10；
> 若沒有，本文件已包含所有必要規格。

---

## 0. 一句話任務
產出一批**乾淨、可逐字引用、已分類、帶 metadata** 的 Markdown 語料檔，分別歸入 5 個
collection 目錄，供後續 bge-m3 嵌入 → Qdrant 檢索 → 引用查核（護欄 G5）使用。

## 1. 紅線約束（違反即作廢，最優先）
1. **來源可查、如實標註。** 綜整自公開來源要在 `source` 註明；自行合成的教學內容要標「合成」。
   嚴禁捏造來源、嚴禁把合成內容偽裝成真實準則。
2. **不做地緣政治判斷、不選邊。** 這是模擬訓練用的**通用**軍事學語料。
3. **語料是「原則」，不是「某局事實」。** 不要寫特定座標的可視/可達/命中結論——那是物理引擎的職責，
   不進語料庫。

## 2. 你要產出什麼：5 個 collection

在 `ai/rag/corpus/<collection>/` 下產出 `.md` 檔。名稱固定，勿增減：

| collection 目錄 | 內容範疇 | 起步目標份數 | 典型段落主題 |
|-----------------|----------|--------------|--------------|
| `doctrine_general/` | **通用軍事準則（無法歸紅/藍者）——實務主力** | 越多越好 | 通用戰術原則、聯合作戰、公開教範整理、智庫分析可引用斷言 |
| `doctrine_blue/` | 真正藍軍特化的準則（若能明確辨別） | 0–3 篇 | COA 產製、機動/火力協調、ROE 樣板 |
| `doctrine_red/` | 真正紅軍特化的準則（若能明確辨別） | 0–3 篇 | 遲滯要領、攻擊/防禦準則、指管慣性 |
| `equipment_specs/` | 裝備參數（通用/公開規格） | 依可得 | 每項裝備一節：射程、感測、速度、補給、防護 |
| `terrain_analysis/` | 地形對機動/火力/掩蔽的「原則」 | 依可得 | 通道/隘口識別、掩蔽運用、高地價值、渡河點 |
| `historical_ops/` | 歷史戰例敘事 + 教訓（公開） | 依可得 | 一則戰例：背景→經過→結果→教訓 |

**分類現實（重要）**：實際可得來源多為**無法歸屬陣營**的公開文獻——例如：
- 美軍 FM 系列：https://armypubs.army.mil/ProductMaps/PubForm/FM.aspx
- CNAS《Hellscape》：https://s3.us-east-1.amazonaws.com/files.cnas.org/documents/Hellscape_DEFENSE_2026-Final.pdf
- DTIC 技術報告：https://apps.dtic.mil/sti/tr/pdf/ADA596797.pdf 、 https://apps.dtic.mil/sti/tr/pdf/ADA225466.pdf

這些**幾乎都歸 `doctrine_general`**。**不要為了填紅/藍而硬分類**——歸錯比留空更糟。
紅/藍 collection 允許長期為空（AI 會自動降級，見 SPEC_FULL §9.0）。

**PDF/掃描/圖檔**：**不要**在本任務裡手工轉錄長篇 PDF。那屬另一個子系統（**SPEC_INGEST.md** /
TASKS O9：文檔轉換管線 PDF→OCR→markdown→人工審核）。本任務只處理**已是文字、可整理成短
markdown 節**的內容；遇到大量 PDF 請在 manifest 標記「交 O9 管線」。

## 3. 分類決策規則（一份文件該進哪個 collection）
- **預設 `doctrine_general`**（`doctrine_side: NA`）——通用軍事準則、公開文獻整理都放這。
- 只有**明確**是我方/友軍特化 → `doctrine_blue`；**明確**是對抗方特化 → `doctrine_red`。
  拿不準就 general，**不要猜**。
- 內容是**裝備數字規格** → `equipment_specs`（`doctrine_side: NA`）。
- 內容是**地形如何影響戰術的通則** → `terrain_analysis`。
- 內容是**過去實際發生的戰役敘事+教訓** → `historical_ops`（`doctrine_side: NA`）。
- 一份文件只歸一類。若橫跨兩類，**拆成兩份**分別歸類，不要混寫。

## 4. 每份文件的精確格式（這是硬規格）

### 4.1 檔頭 front-matter（YAML，必填）
```markdown
---
collection: doctrine_general      # 必須是第 2 節表格 6 個目錄名之一（預設 general）
source: "公開來源摘述 / 或『合成教學』"   # 如實標註
classification: UNCLASSIFIED       # 只允許此值
version: "2026-07"                 # 產出年月
doctrine_side: RED                 # RED / BLUE / NA
---
```

### 4.2 內文結構規則
- **一節一概念**。每個可引用段落用 `##` 標題，並在標題內嵌**穩定錨點 id**：
  `## [RED-DELAY-03] 觀測與掩護`。錨點 id 命名：`<主題碼>-<兩位序號>`，全大寫、連字號。
  引用時的錨點格式為 `doctrine_red/red_delay_ops.md#RED-DELAY-03`（後續系統用錨點而非行號，
  因行號會隨編輯漂移——**所以錨點一旦發布就不要改**）。
- **段落長度**：每節目標可切成約 512 tokens 的語意塊（切塊由入庫管線做，overlap 64）。
  實務上每節控制在 ~150–350 中文字或 ~250–500 英文字，勿寫成千字長段。
- **可逐字引用**：每節至少包含一條**具體、可查核的斷言**——數字、門檻、TTP、順序步驟。
  例：「每一天然隘口 MUST 部署至少 1 個班級觀測所」。空泛口號（如「要靈活應變」）不可當一節主體。
- **語言**：中文或英文皆可（嵌入模型 bge-m3 雙語）。同一份文件內語言一致。
- **無外部連結依賴**：內容須自足，不要求讀者連外——本系統 air-gapped。可在 `source` 註明出處文字，
  但正文不放需要點擊的 URL 當內容主體。

### 4.3 檔名
- 小寫、連字號：`<主題>_ops.md` 或 `<裝備>.md`，例：`red_delay_ops.md`、`blue_coa_process.md`、
  `wheeled_apc_generic.md`。檔名要能一眼看出主題。

## 5. 處理流程（你每產一份文件要做的事）
1. **選題**：依第 2 節缺口挑一個主題。
2. **蒐集**：從公開軍事學、公開教範、學術/百科等來源整理**通則**；或合成教學內容。遵守第 1 節紅線。
3. **一般化**：剝除任何可指認的機密/現役特定部隊細節，轉為通用原則。
4. **結構化**：切成數個 `##` 帶錨點的節，每節放一條具體斷言。
5. **標 metadata**：填 front-matter（collection / source / classification / version / doctrine_side）。
6. **自檢**（見第 7 節）→ 寫入對應目錄。
7. **記入 manifest**（見第 6 節）。

## 6. 交付物
1. 各 collection 目錄下的 `.md` 語料檔（符合第 4 節格式）。
2. 一份 **`ai/rag/corpus/MANIFEST.md`**，表格列出你新增的每一份文件：
   | 檔案 | collection | 主題 | 錨點數 | 來源類型（公開整理/合成） |
3. 若有無法處理或有疑慮（疑似觸及機密、來源不明）的主題，列在 manifest 末端「未處理/存疑」區塊，
   **交回人工判斷，不要自行硬做**。

## 7. 驗收自檢清單（每份文件產出前逐項確認）
- [ ] front-matter 五欄齊全，`collection` 是合法 5 選 1，`classification: UNCLASSIFIED`。
- [ ] 每個 `##` 節都有唯一穩定錨點 id。
- [ ] 每節至少一條具體可查核斷言（有數字/門檻/步驟）。
- [ ] 無機密/管制/可指認現役特定部隊之敏感細節。
- [ ] 內容自足、無外連依賴；語言一致。
- [ ] 已登錄 MANIFEST.md。

## 8. 絕對不要做
- ✗ 不要在語料裡寫特定 hex 的可視/可達/命中判定（那是物理引擎的事）。
- ✗ 不要改動既有已發布文件的錨點 id（會破壞引用）。
- ✗ 不要跨類混寫；不要寫沒有具體斷言的空泛長文。
- ✗ 不要碰 `ai/rag/corpus/` 以外的任何檔案（尤其程式碼、schema、CI 設定）。

## 附錄：一份合格範例（已存在於 repo，照抄格式）
見 `ai/rag/corpus/doctrine_red/red_delay_ops.md`——4 個帶 `[RED-DELAY-0x]` 錨點的節，
每節一條具體斷言，front-matter 完整，明標「合成教學、非真實準則」。以它為黃金樣板。
