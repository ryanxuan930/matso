---
task: WP-C3          # SPEC_V2 §6 WP-C3（乘駐車與隊形）
status: IN_PROGRESS  # 後端完成；前端單位卡切換與 2525 修飾未做
started: 2026-07-31T21:00+08:00
updated: 2026-07-31T21:40+08:00
agent: Opus 5
---

# WP-C3 乘駐車與隊形

## 目標摘要

[JCATS-A p.12,25]：**Mount 是操作要點**——單兵未上車行軍速率過慢，一個機步連
下車走跟上車開差了近一個數量級。p.7,26：五種隊形影響受損與火力發揚。

## 兩個正交的軸，不是一個

- **乘駐車**（`mounted`）：用誰的速度、被打到時傷亡怎麼算。
- **隊形**（`formation`）：行軍速度倍率、面殺傷暴露、火力正面。

兩者相乘。COLUMN 的乘車連走得最快、挨砲最慘；LINE 的下車連火力最全、走得最慢。

## 三個刻意的裁決

### 1. COLUMN 當中性值，不是 LINE

`formation` 缺鍵讀作 COLUMN，**且 COLUMN 的三個係數全是 1.0**。
COLUMN 是「沒有特別展開」的預設，不是「最好的隊形」——
把 LINE 當 1.0 會讓所有既有局憑空獲得火力加成。有測試釘住既有局位元不變。

### 2. 火力正面**不進**目標暴露

隊形有兩個方向相反的效果：橫隊散得開（面殺傷暴露**低**）、正面寬（火力發揚**高**）。
把它們放同一個數字，「我方展開成橫隊」就會同時變成「敵人比較好打我」。
故 `EnvSnapshot` 分成兩欄：`target_exposure_modifier`（目標多好打）與
`shooter_frontage_modifier`（射手打得出多少）。

### 3. 一個令型而不是三個

規格寫「MOUNT/DISMOUNT 令」，實作收成**一個 `FORMATION` 令**（payload 可帶
`formation` 與/或 `mounted`）。三個令型會讓席位表、payload 表、預檢分派、前端下拉
各多兩個分支，而它們表達的是同一件事——宣告本單位要以什麼狀態行動（與 POSTURE 同類）。
一次可以同時改兩者（「下車並展開成橫隊」是一個動作）。
**None 代表不動該欄**：只想下車的令不該把隊形一起重設。

## 差點放進去的一個無聲回歸：`mounted` 是三態不是布林

第一版的 `read_formation` 寫 `bool(state.get(MOUNTED_KEY))`——**缺鍵收成 `False`**，
於是既有局的每個單位都被判定為「已下車」而吃到 0.8 的受彈面折減。
**所有既有局的命中率會無聲下降 20%。**

兩層測試都抓不到：
- **golden 抓不到**：五個案例沒有一個跑直射交戰（`suppression_defense_60` 走的是面射擊，
  而面射擊的 `AreaTarget.formation` 有 `"COLUMN"` 字面預設，剛好是 1.0）。
- **交戰單元測試也抓不到**：它們直接建 `EnvSnapshot`，用的是**欄位預設 1.0**，
  根本不經過接線那一層。

錯在**接線**，而兩層測試都在接線的兩側。是我在提交後自己回頭算了一次
「既有局讀到什麼」才發現的。

修法：`mounted` 改成三態——`None`（從未宣告）／`True`（乘車）／`False`（已下車），
`None` 一律 1.0。並補一條**打在接線上**的測試（`test_an_existing_session_gets_exactly_1_0_from_both_modifiers`），
以及實測 `make_engage_env` 對無鍵單位回 (1.0, 1.0)。

教訓：**中性預設的測試要打在接線層，不是純函數層**。純函數的預設參數天生就是中性的，
測它等於測 dataclass 的預設值；真正會出事的是「接線怎麼把熱狀態翻譯成參數」。

## 順手修掉我自己在 B4 埋的 import 環

`app.sim_params` → `app.adjudication`（套件 `__init__`）→ `seed_equipment`
→ `app.governance`（B4 的簽證閘門）→ `app.sim_params`（部分初始化）→ **ImportError**。

在跑起來的 app 裡因為進入點的 import 順序而不會踩到，但**直接 `import app.sim_params` 就會炸**。
是這張卡要往 SimParams 加係數時撞到的；用 `git stash` 確認過**環在我的改動之前就存在**
（B4 接 `seed_equipment` 時形成），不是本卡造成的——但它是我自己埋的，順手修掉。
修法：`governance/seal.py` 把 sim_params 的 import 移進函式（它真正需要的時機是執行期）。

## 驗收

- 「COLUMN 遭砲擊傷亡 > LINE（同 seed 對照）」——有測試，且**斷言倍率關係對得上係數表**
  （0.7），不是「隨便大一點」。
- 「dismounted target modifier × 0.8」——有測試。
- 中性預設不改變面射擊結果——有測試。

`test_formation.py`（19）。全關卡：pytest 1736、mypy 250、ruff、OpenAPI、前端兩閘門綠。

## 未做（本卡剩餘）

- **`mounted` 尚未影響移動速度**。規格說「mounted 時速度＝載具 profile」，
  而 `resolve_unit_mobility` 是**由編裝導出**的——有載具的單位一律拿到載具 profile，
  沒有「雖然有車但現在用走的」這個狀態。要接上得讓 movement 在 dismounted 時強制走 FOOT，
  那會動到 `UnitMobility` 的解析路徑（本卡只做了隊形的速度倍率）。
- **載具毀損 → 乘員傷亡折算尚未接進裁決**。係數（`crew_casualty_fraction`）已進 SimParams
  且有純函數，但 `resolve_engagement` 還沒有「這次命中打掉的是車還是人」的區分——
  那需要單位編成組成（#48 P5「目標編成組成」那張卡）。**明記而不假裝做完。**
- 前端：單位卡的隊形/乘駐車切換、MIL-STD-2525 的 mounted 修飾符。
