# MATSO — 第二代系統規格（SPEC_V2）
# 差距分析與擴充開發規劃（V2 Roadmap for Engineering Agents）

> 版本 0.1（2026-07-29）。本文件是**架構師交付給工程 agent（Opus 5）的開發藍圖**：
> 以六份外部兵推/演習文獻對照 MATSO 現況（見 [README.md](README.md) 的逐檔盤點），
> 找出結構性差距，並把每一項擴充寫到「可直接開工」的顆粒度。
> 語言慣例同 SPEC_FULL：正體中文敘述、程式識別字/API 欄位一律英文；MUST/SHOULD/MAY 依 RFC 2119。
> **本文件不取代 SPEC_FULL**——SPEC_FULL 仍是既有系統的規格權威；本文件是「下一步做什麼、怎麼做」的權威。
> 開工紀律見 §9（給工程 agent 的執行守則）；每個工作包（WP）開工前先讀其「規格依據」欄指向的章節。

---

## 目錄

1. [文件目的與使用方式](#1-文件目的與使用方式)
2. [參考文獻與證據基礎](#2-參考文獻與證據基礎)
3. [現況基準線：MATSO 已經是什麼](#3-現況基準線)
4. [差距分析總表](#4-差距分析總表)
5. [V2 設計不變量（紅線的延伸）](#5-v2-設計不變量)
6. [工作包詳細規格](#6-工作包詳細規格)
   - [WP-A：AI 誠實化與任務級指揮](#wp-a)
   - [WP-B：演習生命週期與想定體系](#wp-b)
   - [WP-C：裁決保真](#wp-c)
   - [WP-D：分析與決策支援](#wp-d)
   - [WP-E：工程韌性與維運](#wp-e)
   - [WP-F：AI 深化（RAG／評測／MoA）](#wp-f)
   - [WP-G：前端工程健全化](#wp-g)
   - [WP-H：互通與多站演習](#wp-h)
7. [分期路線圖與依賴序](#7-分期路線圖與依賴序)
8. [刻意不做（Non-Goals）](#8-刻意不做non-goals)
9. [給工程 agent 的執行守則](#9-給工程-agent-的執行守則)

---

## 1. 文件目的與使用方式

MATSO 走到今天是一個**能跑完整閉環的陸戰兵推引擎**：想定→下令→裁決→迷霧→AAR，加上 LLM 陣營 AI 自主對抗。
但對照專業演習系統（JCATS/JTLS/SWORD/CMO）與軍方演習實務文獻，它還停在「**模擬引擎**」階段，距離「**演習系統**」
（能支撐一場真正的 CPX：演前整備、席位編組、MSEL 誘導、訓後評量）與「**分析系統**」
（蒙地卡羅批次、MOE 框架、what-if 比較）各有一段清楚可列舉的路。本文件就是那份列舉。

使用方式（給接手的 Opus 5）：

1. **一次一個 WP 子項**。每個子項自成任務卡（建議編號沿用 TASKS.md 流水號，開工時登錄），
   遵守 CLAUDE.md 強制流程：先讀 PROGRESS.md → 建/續 worklog → 綠燈點 commit。
2. 每個 WP 的「實作步驟」給的是**建議切法與檔案落點**，不是逐行指令——落地時以現行程式碼為準，
   發現本文件與程式碼衝突時，量小逕行修正並在 worklog 記錄，量大先回報。
3. 「驗收標準」是完工定義（DoD）。**每一條都要有測試或實測證據**，寫入 worklog。
4. 涉及決定性輸出變更的 WP 都標了「golden：重錄」——重錄前先確認變更是預期的（HOW_TO §8）。

---

## 2. 參考文獻與證據基礎

| 代號 | 文獻 | 性質 | 對 MATSO 的主要貢獻 |
|------|------|------|---------------------|
| [IST160] | NATO STO MP-IST-160-S4-3P（MASA SWORD 系 AI 決策支援論文，25 頁） | 學術/廠商 | 任務級下令＋準則分解、態勢分析圖層、FLOT/FEBA 自動線、what-if 分支、按參謀組（HQ cell）對映功能 |
| [INDSR] | 國防安全研究院特刊《模式模擬與電腦兵棋推演》（2023，97 頁全本；使用者另兩份 PDF 為其節錄） | 智庫分析 | 蒙地卡羅批次（30–50 次）、參數掃描、MER/DR/KR 三層指標、hit/kill/destroy 狀態機、成本效益、回放歸因修模、效基裁決（網電）、Lua/CSV/SQL 分析輸出 |
| [JCATS-A] | 陸院林相涵〈戰術想定如何運用 JCATS 實施兵棋推演——以裝甲旅突穿攻擊為例〉（19 頁） | 軍方實務 | 演習生命週期 17 步 SOP、想定文書體系（一般/特別狀況/訓令）、戰演比與暫停鎖定、參數治理簽證、申請-核覆工作流、引擎級演習約束、訓後檢討「不評勝負」 |
| [JCATS-F] | 陸軍砲兵季刊 153 期〈JCATS 系統簡介暨砲兵教學運用〉（17 頁） | 軍方實務 | 完整砲兵 call-for-fire 鏈、C2 工件（透明圖/信文）流轉、教官誘導迴圈、60,000 實體規模、LV 整合、多解析度 JTLS–JCATS 聯接 |
| [JTLS-F] | MITRE〈JTLS-JCATS Federation Support of Emergency Response Training〉（14 頁） | 工程論文 | HLA 聯邦工程細節：MRM 聚合/解聚、屬性所有權轉移、異質時間同步、Class I–X 補給、CBRN 羽流、民事疏散、白軍軟裁決、response cell |
| [MASA-MS] | MASA〈Multi-Site Command Post Exercise〉（3 頁白皮書） | 廠商 | 多站演習拓撲（主站權威模擬＋Relay 複本＋站點 Admin＋package 分發）——形態參照，非實作依據 |

引用格式：`[代號 p.X]`。各文獻完整精讀存於 workflow 產物（`docs/worklog/docs-readme-specv2.md` 記載位置）；
本文件僅引用與決策相關的部分。

## 3. 現況基準線

完整逐檔解剖見 [README.md](README.md)。與本文件相關的一句話總結：

**已經很強的**：確定性引擎與 golden replay、Neuro-Symbolic 分層（LLM 產令/護欄/物理裁決三權分立）、
移動真實化（A* 地形路由＋道路網＋土地利用＋油耗＋耗損）、聯合兵種交戰（volley/combined/聚合 Lanchester）、
N 陣營關係矩陣與後端迷霧、契約先行工程紀律、每一步都有 worklog 的可考古性。

**結構性弱項（本文件的對象）**，按嚴重度排序：

1. **AI 的迷霧是假的**：`ai_loop` 敵情仍餵 ground truth（IntelService 已可用、未接線）——自主推演的「偵察—情報—決策」閉環名存實亡。
2. **MSEL 只有 schema**：活執行期 `TriggerChecker` 是 NoOp、inject 只落 Ledger 無世界效果——白軍誘導能力（演習系統的心臟）缺位。
3. **零分析能力**：無批次實驗、無 MOE、無 what-if、AAR 無地圖重播——對照 [INDSR] 全書，MATSO 現在「一次跑一局、看事件流」的用法連智庫入門級分析都做不到。
4. **裁決保真斷層**：壓制/姿態/隊形/乘駐車/障礙/煙幕/晝夜全缺；後勤只有油料；砲兵無 call-for-fire 程序。
5. **演習管理缺位**：無演習專案概念、無參數凍結簽證、無席位編組、無申請-核覆、無訓後評量。
6. **韌性債**：活 session 無 checkpoint、refresh token 無撤銷、監控目錄是空的。

## 4. 差距分析總表

嚴重度：★★★＝擋住系統定位升級（引擎→演習/分析系統）；★★＝顯著保真/能力缺口；★＝完善項。
「現況」欄引用 README 對應章節的盤點事實。

| # | 差距 | 文獻依據 | 現況 | 嚴重度 | WP | 狀態 |
|---|------|----------|------|--------|-----|------|
| 1 | AI 敵情用 ground truth，迷霧對 AI 不成立 | —（內部盤點；[IST160 p.6] 分層 AI 原則） | `ai_loop/worker.py` 的 `enemy_visibility` 預設 `ground_truth_enemies`；IntelService 已上線未接 | ★★★ | A1 | ✅ 2026-07-29 |
| 2 | 只有低階令（MOVE/ENGAGE/RESUPPLY），無任務級下令與準則分解 | [IST160 p.4–5]（Attack 任務自動展開）；[JCATS-F p.12–13] | orders 僅三型＋fire_policy；AI 逐令微操 | ★★★ | A2 | ✅ 2026-07-31（四張卡）|
| 3 | G4 no-strike 形同空轉（欄位不匹配＋無資料源） | —（內部盤點） | G4 只認 `target_h3`，AI 令帶 `target_lat/lng`；`no_strike_hexes` 恆空 | ★★★ | A3 | ✅ 2026-07-29 |
| 4 | MSEL 排程執行引擎缺位 | [JTLS-F p.1053,1059]；[JCATS-A p.14–15] | `TriggerChecker` NoOp；inject 無世界效果；DSL 無時間/持續條件 | ★★★ | B2 | ✅ 2026-07-31（a/b/c）|
| 5 | 無演習生命週期管理（整備→實施→撤收） | [JCATS-A p.9–16 圖7] | 無演習專案實體；session 即全部 | ★★ | B1 | ✅ 2026-07-31（a/b/c）|
| 6 | 無想定文書層（一般/特別狀況、訓令、反想定） | [JCATS-A p.10–11,16–21] | 想定=資料 JSON；無敘事文件與發佈節奏 | ★★ | B3 |  |
| 7 | 無參數凍結/簽證治理 | [JCATS-A p.14,25–26] | 裝備庫/參數隨時可改；演習公正性與重播性無保障 | ★★ | B4 | ✅ 2026-07-31 |
| 8 | 無申請-核覆工作流（空偵/火協/申補） | [JCATS-A p.13,15,26]；[JCATS-F p.12–14] | 下令即時生效，無異步審批鏈 | ★★ | B5 | B5.1/B5.2/B5.3 ✅ 2026-07-30；B5.4（標繪分送/殲敵 REPORT）未做 |
| 9 | 壓制/姿態係數恆 1.0；無隊形；無乘駐車 | [JCATS-A p.7,12,25,26] | `EnvSnapshot` 兩係數無來源；TacticalUnit 無 posture/formation/mounted | ★★★ | C1/C3 | C1 ✅ 2026-07-31；C3 ✅ 2026-07-31 |
| 10 | 無障礙工事/工兵裁決（雷區/斷橋/鐵絲網） | [JCATS-A p.5–6,12]；[JTLS-F p.1058] | MapFeature OBSTACLE 只是圖形，不參與裁決 | ★★★ | C2 | ✅ 2026-07-30（前端下令 UI 未做） |
| 11 | 天氣單快照；無晝夜/照明；無煙幕 | [JCATS-A p.7,19] | weather 啟動讀一次；SimClock 有時刻但不影響偵測 | ★★ | C4 |  |
| 12 | comms 粒度後果未接投影（位置凍結/敵情粗化） | —（內部盤點；SPEC_FULL §6.2 MUST） | `intel_granularity`/`position_report_*` 已定義無消費者 | ★★ | C5 | ✅ 2026-07-30 |
| 13 | 多方混戰未接線；聚合門檻寫死；#48 未做 | —（內部盤點） | `resolve_multiway_tick` 已實作未用；threshold 忽略想定欄位 | ★★ | C6 |  |
| 14 | 後勤只有油料；無 Class 體系/彈藥人員裝備補充/修復 | [JTLS-F p.1058]；[JCATS-A p.26–27] | `ResupplySystem` 撥交油料+彈藥；無再訂購水位、無修復、無整補時間 | ★★★ | C7 |  |
| 15 | 無 MRM（聚合↔實體解聚合） | [JTLS-F p.1056–1058]；[IST160 p.4] | 兩種裁決並存但單位粒度固定 | ★★ | C8 |  |
| 16 | 友軍誤傷語意：關係矩陣「阻止」而非「照裁」 | [JCATS-A p.5–6] | precheck 擋友軍目標；成熟系統語意是命令照執行後果照裁 | ★ | C9 |  |
| 17 | 無計畫火力/call-for-fire 鏈/BDA 回報 | [JCATS-F p.12–13]；[JCATS-A p.24,26] | 砲兵是即時 ENGAGE 一種 | ★★ | C10 | ✅ 2026-07-30/31（C10.1–C10.5 五張全數結案）|
| 18 | 無蒙地卡羅批次/參數掃描 | [INDSR] 全書方法論（30–50 次/組） | 一次一局；決定性引擎已是完美地基 | ★★★ | D1 |  |
| 19 | 無 MOE 框架/成本效益指標 | [INDSR p.19–20]（MER/DR/KR、hit/kill/destroy） | 勝負 DSL 只裁勝負；AAR 統計初階 | ★★★ | D2 |  |
| 20 | 無態勢分析圖層/自動戰術線 | [IST160 p.15–17,20] | 單一單位 viewshed/射界有；聯集與戰力比分區無 | ★★ | D3 |  |
| 21 | 無 what-if 分支推演 | [IST160 p.19] | 只能整局重跑；clone+決定性引擎地基已備 | ★★★ | D4 |  |
| 22 | 可行性檢查缺時間維度；無補給支撐時間分析 | [IST160 p.14,18,21] | precheck 只判「打得到/走得到」，不判「趕得及/撐多久」 | ★★ | D5 |  |
| 23 | AAR 無地圖重播；統計有帳目瑕疵；無匯出管線 | [INDSR p.43–46,57–59]；README §5 盤點 | `scrubTick` 不驅動視覺；聚合戰損歸帳單側；無 CSV/批次匯出 | ★★ | D6 | D6.1 ✅ 2026-07-30（地圖重播 + 聚合戰損歸帳已修）；D6.2/D6.3 未做 |
| 24 | 無情境化警告/報告分級 | [IST160 p.14,21] | 事件流平鋪，指揮官自行掃 | ★ | D7 |  |
| 25 | 活 session 無 checkpoint/前滾 | —（內部盤點；SPEC_FULL §3.4） | `sim_runtime` 未傳 checkpointer；RNG 狀態不序列化 | ★★ | E1 | ✅ 2026-07-29 |
| 26 | refresh token 無撤銷/無帳號鎖定 | —（內部盤點） | logout no-op；無 brute-force 防護 | ★★ | E2 |  |
| 27 | RESYNC 契約半套（無 /state 快照端點） | —（內部盤點） | 前端收到後丟棄結果、靠週期重抓兜底 | ★ | E3 | ✅ 2026-07-29 |
| 28 | 監控空殼 | —（內部盤點） | prometheus/grafana 目錄只有 .gitkeep | ★★ | E4 |  |
| 29 | 無負載測試/LOD 降載 | [JCATS-A p.12]（飽和測試×2） | 無工具鏈；TickPacer 只會全域降頻 | ★ | E5 |  |
| 30 | RAG 嵌入器佔位、語料近空、SPEC_INGEST 未實作 | SPEC_INGEST 全份；README §10 | hash 嵌入器；語料 1 份合成檔；eval 3 例 | ★★★ | F1/F2 |  |
| 31 | RoleManager/InvocationLog 未接活執行期 | README §10 | LlmFactionDecider 直連 client；活期無 AI 稽核記錄 | ★★ | F3 |  |
| 32 | 訓後評量（training audience）缺位 | [JCATS-A p.15]；[JTLS-F p.1052–1053] | AAR 不評受訓者；評估點無法預埋想定 | ★★★ | F5 |  |
| 33 | cop.vue 4311 行單體等前端債 | README §8 | 詳見 §WP-G 清單 | ★★ | G1a–G6 | G1a ✅ / G1b ✅ 2026-07-30（4419→951） |
| 34 | 無多站演習/DIS-HLA 互通 | [MASA-MS]；[JTLS-F p.1054–1056]；[JCATS-F p.6–7,17] | 單站部署；無狀態複製層 | ★★ | H1–H3 |  |
| 35 | 無民事/CBRN/災防想定能力 | [JTLS-F p.1058–1059]；[INDSR p.37–40] | 引擎綁陸戰交戰 | ★ | H4（遠期） |  |

## 5. V2 設計不變量

所有 WP 的實作 MUST 保持下列不變量（違反＝重做）：

1. **紅線五條原封不動**（CLAUDE.md/HOW_TO §0）：決定性、AI 不裁物理、fog 只在後端、契約先行、一次一卡。
2. **新裁決一律純函數**：所有新增的物理效果（壓制/障礙/煙幕/晝夜/隊形）進 `core/app/adjudication/` 或同紀律的純模組；
   隨機性只經 `DeterministicRNG` 注入的既有 stream 或新 stream（新 stream 名稱要登錄在 worklog 與 SPEC_V2 附錄）。
3. **任務級指揮的分解器是符號層**（WP-A2）：LLM 選任務與參數，展開成低階令的 decomposer 是確定性 Python——
   這是 [IST160] 高低階 AI 分層在 MATSO 的對應物，也是防幻覺面積擴大的關鍵。
4. **白軍軟裁決是人類特權 API**（WP-B2/B5）：[JTLS-F p.1059] 的白軍調整結果通道只開放給 WHITE_CELL 角色，
   LLM 永遠不得呼叫（護欄層擋 + API 層 RBAC 擋，雙重）。
5. **分析功能不汙染活演習**（WP-D）：蒙地卡羅/what-if 一律跑在**複製的 session**（既有 clone 機制），
   絕不在進行中的演習 session 上做實驗；批次跑的 session 標記 `purpose=ANALYSIS` 不進演習列表。
6. **每個新參數都進 PARAMS.md 四層分類**（#93 紀律）：預設值＝現行為，golden 不因參數機制本身而變。
7. **多人演習的公正性優先於便利**：暫停鎖定、參數凍結、審批鏈等「管制語意」寧可嚴格（[JCATS-A p.5,14]）。

---

## 6. 工作包詳細規格

<a id="wp-a"></a>
### WP-A：AI 誠實化與任務級指揮

#### ✅ WP-A1 AI 敵情接上真實情報（迷霧誠實化）　★★★｜golden：不動（AI 不在 golden 路徑）

> **✅ 已完成（2026-07-29）**——worklog `docs/worklog/ai-fog-honesty.md`。實測：RED 見 22/真實 23、YELLOW 25/26。
> 追加的退回開關 `ai_ground_truth`（預設 false）供 WP-D1 的有無迷霧對照實驗。


**動機**：`ai_loop/orchestrator.py` 組 `FactionWorkerDeps` 時，`enemy_visibility` 仍用感測 NoOp 時代的
`ground_truth_enemies`——AI 指揮官全知敵方存活單位位置。SensorSweepSystem（#97）與 `IntelService` 已上線，
協定欄位（`EnemyVisibility`）已備好，**這是純接線工作**。分層 AI 的前提是各層拿到「該拿的資訊」[IST160 p.6,11]。

**規格**：
- AI 陣營的 context builder MUST 改以該陣營的 `IntelContact` 投影（同 `GET /intel` 的後端過濾語意）供應敵情：
  contact 的 `fidelity`（DETECTED/CLASSIFIED/IDENTIFIED）決定 LLM 看得到的欄位
  （DETECTED＝只有概略位置與時間戳；IDENTIFIED＝含型號/編制）。
- 盟軍（ALLIED）單位照 `units` 共享視圖供應（#91 語意），不經 contact。
- `recent_events` 欄位一併補上：取該陣營受眾可見的最近 N 筆 Ledger 事件（複用 broadcaster 的受眾判定）。
- MUST 提供退回開關（`ai_ground_truth=true` 於 session 的 ai_config，預設 false）供對照實驗——
  這本身就是 WP-D1 的第一個實驗題目（AI 有/無迷霧的勝率差）。

**實作步驟**：
1. `core/app/ai_loop/context.py`：新增 `contacts_from_intel(db, session_id, faction, relations) -> EnemyVisibility`，
   內部即 `intel/store.py` 的查詢（複用，勿重寫過濾）。
2. `orchestrator.py` `start_ai_workers`：以 (1) 取代 `ground_truth_enemies`；讀 ai_config 的 `ai_ground_truth` 開關。
3. `worker.py`：`recent_events` 接 `state/ledger` 查詢（受眾過濾後截尾 N=20）。
4. 測試：三陣營局，RED 未被 BLUE 偵測 → BLUE 的 AI context 不含該單位；fidelity=DETECTED 時 context 無型號欄位；
   開關開啟時行為回到現況（有測試釘住，防止悄悄退化）。

**驗收**：實跑一局自主推演，從 `AIInvocationLog`/worker 遙測抽查 prompt——敵情條目數 ≤ 該陣營 contact 數；
關掉感測器的對照局中 AI 應「找不到敵人」而下偵察傾向的令（行為驗證記 worklog）。

**陷阱**：contact 是「最後已知位置」——單位移走後 AI 會打空點，這是**正確行為**（迷霧的本義），
不要「順手修好」；AAR 敘事可標記「基於過時情報的攻擊」供教學。

#### WP-A2 任務級下令與準則分解器（Mission Orders + Doctrinal Decomposer）　★★★｜golden：**不重錄，改為新增案例**（原本寫「重錄」，開工前查證後推翻）　✅ 2026-07-31（四張卡）

> ✅ **完成**——worklog `docs/worklog/mission-orders.md`。新增 golden `mission_seize_60`，
> **既有四個未動**。**與規格不同 / 規格沒點破的實作裁決**：
> ① **golden 不重錄**（見下方原本就寫在標題的更正）。
> ② **分解器一次只推一步**，不是一次展開整個計畫：一次吐出全部子令等於在還沒接敵時就先下好
>   ENGAGE，而那些令的目標是分解當下的 contact——`IntelContact` 沒有存活性欄位，contact 永不過期。
> ③ **對鬼 contact 下 ENGAGE 是對的**（迷霧下該有的行為），**不可以**為了修掉它去查 DB 核對；
>   而**階段推進只看己方單位狀態**，以「無敵蹤」當佔領條件的話任務永遠到不了 HOLDING。
> ④ 迷霧陷阱做成**靜態約束**：`decomposer.py` 的 import 白名單由測試釘住
>   （禁 `app.models`/`app.state`/`sqlalchemy`），讓「有沒有偷看」變成讀簽名就能回答的問題。
>   地形**刻意不走 world_view**——兩者共用一個參數，這個問題就不再讀得出來。
> ⑤ Kernel 的任務槽位給 **NoOp 預設**而非必填（9 個建構點；必填會讓四個 golden 噴 `TypeError`，
>   而那看起來像 golden 壞掉）。
> **補掉四個 fail-open 的洞**（全部不報錯、只靜靜放行）：`run_precheck` 對未知 payload
> `all([]) is True`、`_PAYLOAD_MODELS` 未登錄則跳過驗證、`ai_output.schema.json` 缺 enum 會擋掉
> **整個決策**、`orders_bridge` 的 `else: return None` 靜靜剔除 100% 的 MISSION 令。
> **並補掉兩個實質護欄洞**：`_STRIKE_ORDER_TYPES` 原本只有 ENGAGE（FIRE_MISSION 與 MISSION
> 都不受禁射區約束）；`UnitTargetLocator.locate` 的註解宣稱支援 MISSION objective 而**實際不支援**，
> 且 G4 對 locate 回 None 的政策是不擋——等於打進禁射區的 SEIZE 直接穿過 G4。

> ⚠ **修正原本的 golden 判斷**（2026-07-31，A2 開工前的偵察）。本節原本寫「golden：重錄（新增令型）」，
> 但逐檔追下去**不成立**：`core/tests/replay/` 的四個案例都是**手搭的純記憶體 Kernel**
> （`scenarios.py` 自建 order source 與 adjudicator），**不碰 `OrderType`、不碰 DB、不走 `sim_runtime`**。
> 新增一個列舉成員、一個 `decomposer.py`、一個 `mission_runtime` 子系統，
> 沒有任何一條路會改到那四個雜湊。
>
> 正確做法是 **WP-C1 用過的那一招：新增第五個 golden 案例**（`mission_seize_60`），
> 讓 MISSION 分解有自己的漂移偵測，同時讓既有四個雜湊維持成未被污染的歷史基準。
> **重錄會摧毀 golden 的唯一價值**——那四個雜湊之所以有用，正是因為它們沒有被人為重設過。
>
> 另記一個**看起來像 golden 壞掉、其實不是**的情況：若 `mission_runtime` 以**必填** kwarg 進
> `Kernel.__init__`，四個案例會噴 `TypeError` 而不是雜湊不符。那要補 NoOp 預設，
> **不是**去跑 `rerecord_golden.py`。動手前先讀斷言訊息。

**動機**：[IST160 p.4–5] 的核心論證：成熟系統下的是「任務」（Attack(axis, objective, limit lines)），
由準則庫展開成路徑/梯隊/交戰/脫離；一人可指揮整旅。MATSO 現在人與 LLM 都在微操三種低階令——
LLM 每個心跳要重新推理「下一步走哪」，呼叫頻率高、幻覺面積大。把分解交給符號層正是 Neuro-Symbolic 的本義。

**規格**：
- 新增 OrderType：`MISSION`。payload：`{mission_type, params}`。V2.0 先做四種任務型：
  - `SEIZE`（奪佔）：`{objective: latlng|h3, axis?: latlng[], limit_line?: latlng[]}` →
    分解：主力沿 axis 機動（MOVE 序列）→ 抵達 objective 外圍後對區內敵 contact 逐一 ENGAGE → 佔領後轉 `HOLD`。
  - `DEFEND`（防守）：`{area: ring, orientation_deg}` → 就位（MOVE）→ 設 posture=DEFENSE（WP-C1）→ 對進入射界之敵自動 ENGAGE（受 fire_policy）。
  - `SCREEN`（掩護幕）：`{line: latlng[]}` → 分派下級單位沿線佔位 → 偵測到敵→回報不接戰（fire_policy=HOLD_FIRE）→ 受壓後退至後方 phase line。
  - `MOVE_MARCH`（行軍序列）：`{route: latlng[], march_order: unit_id[], spacing_km}` → 按序出發、維持間距（分進點/時間管制點語意的最小版，[JCATS-F p.10–12]）。
- 分解器 `core/app/orders/decomposer.py`：**純同步函數** `decompose(mission, world_view) -> list[LowLevelOrder]`，
  world_view 只含該陣營可見資訊（迷霧一致性）。分解發生在 Kernel drain 時（admit 階段），
  分解出的子令帶 `parent_order_id` 落 DB（可追溯、可取消整個任務）。
- 任務是**有狀態的**：Kernel 每 tick 評估任務進度（`mission_runtime.py`），
  階段轉換（機動中→攻擊中→已佔領）記 Ledger 事件 `MISSION_PHASE_CHANGED`；
  被殲/彈盡等失敗條件 → `MISSION_FAILED` + 單位轉 HOLD。
- LLM 側：decider 的 OUTPUT_INSTRUCTION 加入 MISSION 令型與 mission_type 詞彙表；
  G3 feasibility 對 MISSION 令檢查 objective 可達性（複用 movement precheck）。
- 人類側：COP 右鍵選單加「下達任務…」（選任務型→地圖畫 objective/axis→送出）。

**契約**：`contracts/core_api.yaml` orders schema 加 MISSION 令型與 payload $defs；
`ws_protocol.md` 加 `MISSION_PHASE_CHANGED`。**先改契約再實作**。

**實作步驟**（建議 4 張卡）：
1. 契約 + `decomposer.py` 純函數 + 單元測試（各任務型的分解快照測試）。
2. `mission_runtime.py` 接進 Kernel（在 movement 之前評估）+ 事件 + golden 重錄。
3. LLM 詞彙表 + G3 擴充 + 自主推演實測（觀察 LLM 是否改下任務令、心跳負載變化記 worklog）。
4. COP 下令 UI + AAR 任務時間軸（任務條顯示各階段起訖）。

**驗收**：一道 SEIZE 令使一個連自主完成「機動→接敵→佔領→防守」全程無人工介入；
取消任務令連帶取消所有子令；golden 重錄後 6 案例綠；LLM 平均每決策心跳產生令數下降（記錄前後對比）。

**陷阱**：分解器讀的 world_view 必須走迷霧投影，否則 AI 經由任務分解「偷看」ground truth，A1 白做。
任務狀態機不要做成 async——它是 tick 內的純狀態轉移，進 Kernel 流程。

#### ✅ WP-A3 修復 G4 no-strike 護欄（欄位匹配＋資料源）　★★★｜golden：不動

> **✅ 已完成（2026-07-29）**——worklog `docs/worklog/g4-no-strike.md`。規格點出兩個斷點，實作時發現**第三個**：
> `GUARDRAIL_INTERVENTION` 自 O6.2 起無任何 production 呼叫端 → AAR 的「護欄攔截 N 次」恆為 0。


**動機**：內部盤點發現 G4 只認 `target_h3`，而 AI 令實際帶 `target_lat/lng`（MOVE）或 `target_unit_id`（ENGAGE）——
**G4 從未真正攔過任何東西**；且 `no_strike_hexes` 由 deps 傳入但恆為空（想定/白軍都沒有寫入路徑）。
這是護欄鏈的實質漏洞（G1–G6 中 G4 空轉）。

**規格**：
- 想定 schema `scenario.schema.json` 增 `no_strike_zones: [{name, geometry: polygon|circle, class: NO_STRIKE|RESTRICTED_FIRE}]`；
  loader 載入時轉 h3 res-8 格集存 DB（session 層級，白軍可於局中增修——記 Ledger）。
- G4 判定改為：ENGAGE/MISSION 令 → 解析目標單位當前位置或 objective 座標 → `latlng_to_cell` → 對格集查
  （不再依賴令面自帶 h3）；MOVE 令不擋（開進禁射區不是違規，打進去才是）。
- RESTRICTED_FIRE 區：不硬擋，改標 `escalate`（G6 白軍確認流，見 WP-B5）。
- 白軍 UI：地圖編輯器的區域標註可標記為 no-strike（`kind=CONTROL_MEASURE` + `attributes.control_class`），
  儲存時同步 session 禁射格集。

**驗收**：AI 對禁射區內目標下 ENGAGE → 被 G4 攔、記 `GUARDRAIL_INTERVENTION`；
人類下同樣的令 → precheck 警告＋須明確 override（override 記 Ledger 供 AAR 追究）；
單元測試涵蓋 lat/lng 與 unit_id 兩種目標表達。

#### WP-A4 LLM 角色扮演小組（Response Cell as a Service）　★★｜golden：不動

**動機**：[JTLS-F p.1053] 的 response cell 是「模擬↔受訓者之間的角色扮演轉譯層」（扮演上級/友鄰/民政），
傳統上人力密集；[JCATS-A p.28–29] 的專業操作手制度同理。MATSO 的 LLM 架構天然適合——
這不是差距而是**差異化機會**：文獻用人力解決的，MATSO 用 LLM 解決。

**規格**：
- 新 AI 角色 `RESPONSE_CELL`（進 `ai/matso_ai/roles.py` registry）：輸入＝該席位可見的態勢 + MSEL 注入內容，
  輸出＝**訊息**（不是命令）：上級指導電文、友鄰通報、民政請求。走新的 `Message` 實體（WP-B5 的信文機制）。
- 護欄：RESPONSE_CELL 產出經 G1（schema）+ G2（一致性）即可——它不產令，G3–G4 不適用；
  但 MUST 打上 `ai_generated=true` 標記，前端顯示 AI 徽章（訓練透明性的反向要求：受訓者事後要能知道哪些是 AI）。
- 白軍可在 MSEL 事件上勾「由 AI 扮演發送方」——MSEL 注入（WP-B2）觸發時自動請 RESPONSE_CELL 生成電文。

**驗收**：MSEL 事件「上級變更任務重點」觸發 → BLUE 指揮官席位收到一封語境正確的上級電文（引用當前戰況）；
AAR 可過濾出所有 AI 生成訊息。

<a id="wp-b"></a>
### WP-B：演習生命週期與想定體系

#### WP-B1 演習專案（Exercise）實體與生命週期　★★｜golden：不動　✅ 2026-07-31（B1a/b/c）

> ✅ **完成**——worklog `docs/worklog/exercise-lifecycle.md`。切為 B1a（實體 + 階段機 + 稽核 +
> 整備勾稽 + 掛/卸 session）、B1b（撤收建檔 + 銷毀模式）、B1c（lobby 演習分頁）。
> **與規格不同的實作裁決**：
> ① **刪掉契約裡的 `/sessions/{id}/lifecycle`**——它有 `START|PAUSE|RESUME|END|ROLLBACK` 的描述字串，
>   **無 security、無 schema、無實作**，長得就像本卡要的端點。採用它等於繼承一個未認證的規格殘骸；
>   實際的每局控制早就在 `POST /sessions/{id}/control`。
> ② 稽核表加**演習內單調 `seq`**（規格只說「專屬 audit 表」）：以 `(at, id)` 排序等於隨機排序
>   （同一請求內兩筆 `at` 相同、uuid 當 tiebreak），讀不出「先勾稽才推階段」的因果。
> ③ **階段不可倒退**（規格未明說）：B4 的簽證與稽核的意義都來自單調；要重來就開新演習。
> ④ 掛 session **用既有的局，不用 `clone_session`**——那條路徑會掉七個想定衍生欄
>   （msel/roe/mobilityOverrides/noStrikeZones/…），預推局會沒有 MSEL、沒有 ROE 地跑（已記 Backlog）。
> ⑤ **銷毀限 UserRole.ADMIN**（規格寫「管理員權限」）：`is_omniscient` 包含每一位白軍幕僚，
>   用它等於把不可逆的銷毀開放給整個統裁組。這是 repo 裡第一個嚴格 ADMIN 閘門。
> **順手修掉的既有缺陷**：`delete_session` 的手寫刪除清單漏了 Message/Request/FirePlan/FirePlanTarget
> （prisma 裡無 FK 故不噴錯，列永遠孤兒化），且完全不清 Redis——對一個以資料銷毀為目的的功能
> 而言那是資料殘留。改成由 SQLAlchemy mapper registry 自省。

**動機**：[JCATS-A p.9–16 圖7] 的 17 步 SOP 顯示「一場演習」遠大於「一局模擬」：
整備會議、想定發佈（D-45）、系統飽和測試、預推、正式實施、每日檢討、撤收建檔。
MATSO 現在 session 即全部——沒有把多次預推、正式局、檢討會裝在一起的容器。

**規格**：
- 新表 `Exercise`（Prisma migration）：`{id, name, phase: PREP|REHEARSAL|EXECUTION|REVIEW|ARCHIVED, scheduleJson, checklistJson, createdBy}`；
  `WargameSession` 加 `exerciseId?`（NULL＝獨立局，現況零遷移）與 `sessionRole: REHEARSAL|MAIN|ANALYSIS`。
- 生命週期 API：`POST /exercises`、`PATCH /exercises/{id}/phase`（階段推進記 Ledger 型事件到專屬 audit 表）、
  checklist 項目勾稽（整備會議×3、預推完成、參數簽證完成——見 WP-B4）。
- 前端：lobby 增「演習」分頁——演習卡片內列其 sessions（預推/正式/分析），階段徽章。
- 撤收：`ARCHIVED` 時觸發資料建檔導出（AAR bundle + Ledger 匯出 + 想定包），
  並依 [JCATS-A p.16] 的資安要求提供「銷毀模式」（匯出後硬刪 session 資料，需二次確認 + 管理員權限）。

**驗收**：建演習→掛 2 個預推局＋1 正式局→階段推進留痕→撤收產出 bundle；
獨立 session 流程完全不受影響（回歸測試）。

#### WP-B2 MSEL 排程執行引擎與白軍誘導迴圈　★★★｜golden：重錄（TriggerChecker 接入 tick）　✅ 2026-07-31（B2a/b/c）

> ✅ **2026-07-31 完成 B2a/B2b/B2c**——worklog `docs/worklog/msel-runtime.md`。
> 動手前 MSEL **三層都是斷的**：`check()` 只回 LedgerEvent（改變不了世界）、
> 活執行期傳 `NoOpTriggerChecker`（從未被呼叫）、且 `create_session_from_scenario`
> **從不持久化 `loaded.msel`**（執行期讀不到任何腳本事件）。三個斷點各自獨立。
> **與規格不同的實作裁決**：`held_for`/`after_ticks_of` 需要跨 tick 記憶，
> 而條件評估是純函數——記憶放進 `TriggerContext` 由引擎維護，並**進 checkpoint 信封**
> （不進的話重啟會把所有 `once` 條目重新武裝，D+2 增援每次重啟都再來一次）。
> **golden 未重錄**：無 MSEL 的既有案例中 `MselRuntime.check()` 第一行就回 `[]`，位元不變。
> 剩餘：含 MSEL 的 golden 案例、白軍 `delay`（目前只有 fire/skip）。

**動機**：[JTLS-F p.1053,1059]：「不適合模擬的想定元素以 MSEL 腳本事件注入，且注入與否視演習流程動態決定」；
[JCATS-F p.9–10] 的教官誘導迴圈（狀況發佈→處置→回報→AAR→下一狀況）。MATSO 的 MSEL 只有 schema 雛形，
活執行期 `TriggerChecker` 是 NoOp，inject 只落 Ledger 沒有世界效果——演習系統的心臟缺位。

**規格**：
- **條件 DSL 擴充**（`core/app/scenario/triggers.py`）：既有 `unit_in_region`（bbox）之外，MUST 增：
  `at_tick`（絕對時間）、`after_ticks_of`（相對於另一事件）、`held_for`（條件持續 N ticks）、
  `unit_in_polygon`（真多邊形）、`strength_below`、`contact_established`、`manual`（白軍手動扣板機）。
  組合子 `all_of/any_of/not`。**DSL 是資料不是程式**——禁止 eval。
- **MSEL 執行器** `core/app/scenario/msel_runtime.py`：實作 `TriggerChecker` Protocol，接進 Kernel tick loop
  （取代 NoOp）。每 tick 評估 pending MSEL 事件的觸發條件；觸發時執行 `actions`：
  - `SPAWN_UNITS`（增援生成：ORBAT 片段 → 建 TacticalUnit + 配裝 + 播熱狀態）
  - `MODIFY_UNIT`（戰損/補充/位置修正——白軍軟裁決的機器化出口，[JTLS-F p.1059]）
  - `WEATHER_OVERRIDE`（天氣覆蓋，接 WP-C4）
  - `MESSAGE`（發信文給指定席位，可掛 WP-A4 的 AI 扮演）
  - `PAUSE`（自動暫停等白軍講評）
- **白軍動態取捨**：`manual` 觸發型在白軍控制台列成「待命注入」清單，一鍵扣發；
  非 manual 事件白軍可 `skip/delay`（記 Ledger，AAR 顯示「原定 vs 實際」）。
- 決定性：MSEL 評估在 tick 內、輸入只有世界狀態與 DSL——天然決定性；`manual/skip/delay` 是**白軍輸入**，
  屬於與「下令」同類的外部輸入，經 DB 佇列進 tick（replay 時照錄照放）。

**實作步驟**：DSL 擴充+測試 → msel_runtime 接 Kernel（golden 重錄：無 MSEL 的 6 案例應位元不變，
新增 1 個含 MSEL 的 golden 案例）→ 白軍 UI（待命注入面板）→ SPAWN_UNITS 端到端。

**驗收**：想定含「D+2 紅軍增援一個營於北岸生成」→ 到時自動出現且各陣營迷霧正確（未偵測不可見）；
白軍延遲注入留痕；含 MSEL 的 golden 案例可重播。

**陷阱**：SPAWN_UNITS 生成的單位 id 必須決定性（由 msel event id 派生，禁 uuid4()）；
runner 啟動時建構的 WeaponResolver 快取要能吸收局中新單位（連動 README §5 已知缺口——
解法：resolver 改惰性查詢＋失效通知，不要全域重建）。

#### WP-B3 想定文書層與 LLM 輔助產製　★★｜golden：不動

**動機**：[JCATS-A p.10–11,16–21]：想定不只是資料，是文書體系——一般狀況（逆序式設計：先定 D 日態勢再回推）、
特別狀況（四要素、力空時合理）、訓令（統裁部以「指揮官全程作戰構想」誘導）、想定/反想定、D-45 發佈。
這正是 LLM 的甜蜜點：資料（ORBAT/MSEL）→ 敘事文書的雙向輔助。

**規格**：
- `Scenario` package 增 `documents/` 目錄位（zip 內）：`general_situation.md`、`special_situation.md`、
  `op_order.md`（訓令）、per-faction 版本（迷霧：各陣營只看得到自己的特別狀況與訓令）。
- 想定編輯器增「文書」分頁：Markdown 編輯 + **LLM 輔助生成**按鈕（把 ORBAT/地域/MSEL 摘要餵給
  `SCENARIO_WRITER` 新角色，產出草稿；人工定稿）。生成走 AI_BARE 即可（不需 RAG）。
- 反想定支援：一鍵「以紅軍視角重述」——同一份資料生成對方的一般狀況（[JCATS-A p.17] 甲乙軍對稱想定）。
- 發佈：文書隨想定包 export/import；session 開局時各陣營指揮官席位可讀本軍文書（新 API
  `GET /sessions/{id}/documents?as_faction=`，後端過濾）。

**驗收**：tutorial 想定補齊全套文書；BLUE 席位讀不到 RED 訓令；LLM 草稿與人工定稿的 diff 留檔（供 F 系列評測）。

#### WP-B4 參數治理：凍結、簽證、審計　★★｜golden：不動　✅ 2026-07-31

> ✅ **完成**——worklog `docs/worklog/parameter-seal.md`。
> **與規格不同的三個實作裁決**（規格的寫法在這個 codebase 上不成立）：
> ① **鎖住的是全域，不是「該演習關聯 session」**。`EquipmentTemplate` 是全域武器庫（無 sessionId）、
>   `SimParams` 存在 `SystemConfiguration.integrationConfig["sim"]`（DB 單一列），
>   而 `POST /equipment-templates` 與 `PUT /system/config` **根本不帶 session_id**——沒有東西可以 scope。
>   實際規則只能是「有任何演習簽證生效中，全域寫入一律拒絕」。驗收條文的「散局不受影響」
>   講的是**開局不被拒**，不是寫入保持開放。
> ② **簽證是 REHEARSAL 期間的明示動作**（`POST /exercises/{id}/seal`），不是進 EXECUTION 的副作用。
>   照規格做會**死鎖**：`params_sealed` 同時是離開 REHEARSAL 的必要勾稽項，
>   而勾是進 EXECUTION 以後才發生的。
> ③ **`PUT /system/config` 選擇性拒絕**：該端點同時寫 `ai.*`（H 層，規格明說不凍）與 `sim`；
>   整條擋掉會讓白軍演習中連 LLM 端點都換不了。且**值沒變就放行**（前端每次存檔送整包）。
> **另加一條規格沒有的解鎖路徑** `DELETE /exercises/{id}/seal`：沒有它，一場被忘記的演習會讓
> 全域武器庫**永遠唯讀**（`active_seal` 看 phase，而 phase 推不動就卡住）。
> 它改變的是「有沒有簽證」這個事實本身且進稽核軌跡——**不是**在寫入端點加 `force` 旗標（紅線 3 的精神）。
> **擋掉一個很難查的自傷**：`seed_equipment._upsert_templates` 跑在每次由想定開局時且會覆寫既有範本，
> 簽證期間照做的話，在已簽證的演習裡新開一個預推局就會靜靜改掉被鎖的表，
> 然後該演習每一局都因雜湊不符而拒起、毫無痕跡。改成只擋覆寫、不擋新建。
> **已知界線**：`docs/PARAMS.md` 的 P 層還有 25+ 個硬編模組常數沒進 `SimParams`，改不了也就鎖不了；
> 簽證只涵蓋目前真的可調的子集，這比宣稱「R+P 全鎖」誠實。

**動機**：[JCATS-A p.14,25–26]：動態參數（偵測/運動/交戰距離、戰損率、攜行量）想定編輯期可調，
「參數確認後簽證鎖定不得再改」。MATSO 裝備庫/SimParams 隨時可改——多人演習的公正性與重播性沒有制度保障。

**規格**：
- `Exercise`（WP-B1）階段機掛鉤：進入 `EXECUTION` 時對關聯 sessions 執行 **parameter freeze**：
  - 快照 EquipmentTemplate 全表 + SimParams + mobility_matrix 版本雜湊 → 存 `ParameterSeal`
    （`{exerciseId, sealedAt, sealedBy, contentHash, snapshotBlob}`）。
  - 凍結後：裝備模板/SimParams 的寫入 API 對該演習關聯 session MUST 拒絕（403 `PARAMS_SEALED`），
    白軍例外通道走 MSEL `MODIFY_UNIT`（留痕）而非直接改模板。
- 簽證驗證：任何 session 啟動時比對 seal hash——不符即拒起（防「演習中偷改參數重啟」）。
- 靜態/動態參數白名單：PARAMS.md 的 H/R/C/P 分層直接複用——凍結對象＝R+P 層；H 層（UI 偏好類）不凍。

**驗收**：凍結後改裝備模板被 403；篡改後 session 重啟被拒且事件留痕；未掛演習的散局不受影響。

#### WP-B5 申請-核覆工作流與 C2 信文　★★｜golden：不動（新令型不進現有 golden 路徑）

> **B5.1（席位模型）✅ 已完成（2026-07-30）**——worklog `docs/worklog/seat-model.md`。
> `SessionParticipant.seat_role`（可為 NULL）+ 下令權 registry（`core/app/seats`）+ 名冊 UI。
> ⚠ **NULL ＝ 完全沿用 role 既有權限**（使用者裁示）：既有局零行為變更，有專門測試釘住。
> 席位 → 可下令型別採保守表（COMMANDER 全／S3_OPS 機動／FSO_FIRES 火力／S2·S4·OBSERVER 無），
> 收在單一 registry，調整分工只改那一張表。新錯誤碼 `ORDER_SEAT_DENIED`（與
> `ORDER_PERMISSION_DENIED` 刻意分開：「不是你的單位」vs「不是你的職掌」處置不同）。
> **審批權屬 B5.2**，本卡只做下令權。
>
> **B5.2（信文 / 申請-核覆）✅ 已完成（2026-07-30）**——worklog `docs/worklog/c2-messaging.md`。
> `Message`/`Request` 實體 + 審批鏈 + 想定層配額（開局快照）+ WS 席位受眾 + COP 信文小工具。
> ⚠ 規格在 WP-B5 底下列的五件事遠超一張卡，故切為：B5.2（信文+審批鏈）、
> **B5.3 曲射火協 gate**、**B5.4 標繪分送 + 殲敵自動 REPORT**。
> 關鍵設計：配額用罄**落 DENIED 而非拒收**（留痕才可評分）；`APPROVED` 與 `EXPENDED`
> 分開（一張核准單只能兌現一次）；席位受眾**只能收窄**（動 `faction_filter` 前先釘 14 格真值表）。
> 已知界線：**配額是整局總量非每日**——SimClock 無「模擬日」概念，硬做會是假的。
>
> **B5.3（曲射火協 gate）✅ 已完成（2026-07-30）**——worklog `docs/worklog/fire-approval-gate.md`。
> 想定開關 `indirect_fire_requires_approval` + `EngagePayload.fire_request_id` +
> 預檢 gate（`ORDER_FIRE_APPROVAL_REQUIRED`）+ 令收下時兌現核准單。
> ⚠ **與 ROE 武器禁令的做法刻意不同**：ROE 只擋「指名了被禁武器」，其餘交裁決層逐武器篩；
> 火協是**單一道令的許可**，照抄 ROE 會讓「不指名武器」成為繞過的洞。故未指名武器但
> 單位持有曲射武器時同樣要求核准單（訊息明講「請附核准單或指名直射武器」）。
> 前端尚未提供選核准單的 UI，C10 一併做。

**動機**：[JCATS-A p.13,15,26] 的空偵申請/申補憑單/曲射火協核准；[JCATS-F p.10–14] 的信文下令與
透明圖分發——「指參程序的磨練」靠的是異步審批鏈與 C2 工件流轉，不是即時生效的按鈕。

**規格**：
- 新實體 `Message`（信文）：`{id, sessionId, fromSeat, toSeat|toFaction, kind: FREE_TEXT|REQUEST|APPROVAL|REPORT,
  refId?, bodyJson, tick, readAt?}`。WS 推播（受眾＝收件席位/陣營）。
- 新實體 `Request`（申請單）：`{kind: AIR_RECON|FIRE_SUPPORT|RESUPPLY_VOUCHER, params, status:
  PENDING|APPROVED|DENIED|EXPENDED, quota 消耗}`。流程：下級席位送出 → 上級/白軍席位核覆 →
  APPROVED 後轉為對應效果（AIR_RECON→一次性感測掃描事件；FIRE_SUPPORT→解鎖一次曲射任務；
  RESUPPLY_VOUCHER→允許一次 RESUPPLY）。每日（模擬日）配額於想定定義（[JCATS-A p.13] 空偵架次）。
- 曲射火協 gate（可選開關，想定層級 `indirect_fire_requires_approval`）：開啟時 ARTILLERY/MISSILE 的
  ENGAGE 令須掛已核准的 FIRE_SUPPORT request id，否則 precheck 拒絕。
- 標繪分送 [JCATS-F p.24–25]：MapFeature 增 `shared_to: seat[]`——把一張標圖「傳送」給特定席位
  （在其 COP 顯示、含來源徽章）；殲敵回報自動生成 REPORT 信文給同陣營砲兵席位（防重複打擊）。

**驗收**：想定開火協 gate 後，未核准的砲擊令被拒；核准流全程留痕可在 AAR 重建
（申請時間→核覆時間→執行時間，正是 [JCATS-F p.14] 的可評分事件鏈，接 WP-F5）；
配額用罄後申請自動 DENIED。

**依賴**：席位（seat）概念——RBAC 需從「角色×陣營」擴充為「席位」（同陣營內多席分工，
[JCATS-F p.9–10] 每指揮組/排長/觀測官一席）。實作為 `SessionParticipant.seat_role`
（COMMANDER/S2_INTEL/S3_OPS/FSO_FIRES/S4_LOG/OBSERVER），下令與審批權按 seat_role 細分——
此為 WP-B5 的第一張卡。

#### ✅ WP-B6 想定資產補齊　★｜golden：不動

> **✅ 已完成（2026-07-29）**——worklog `docs/worklog/scenario-assets.md`。四項全做，另補**規格未列但必要**的兩項：
> orbat `equipment`（沒有它「兩個新官方想定」只能全員同一把步槍）與 condition DSL 的**載入時驗證**
> （`tutorial-platoon` 用了不存在的 type `eliminate`，想定照樣載入、整局不判勝負）。
> ⚠ 機動覆寫**不得改變可通行性**——A* 在 terrain 容器看不到想定覆寫，改了會讓規劃與執行分歧。


盤點欠帳的收尾卡：`roe.yaml` 的 JSON schema（規格宣告了、schema 缺）；battalion-defense 與
joint-defense 兩個官方想定建置（[JCATS-A] 的裝甲旅突穿攻擊想定結構——一般/特別狀況、對稱兵力、
統裁誘導——是絕佳藍本，建為第三個官方想定 `armor-breakthrough`）；`scenario_to_dict` 修復 `fixed`
旗標遺失（roundtrip bug，先修）；`overrides/`（mobility_matrix 想定覆寫）載入端實作。
**驗收**：三個官方想定 export→import→export 位元一致；含 roe/msel/文書/no-strike 的完整示範。

<a id="wp-c"></a>
### WP-C：裁決保真

> 本群全部遵守不變量 2（純函數＋DeterministicRNG）。多數子項會改變決定性輸出→golden 重錄；
> 每一項的係數 MUST 進 `SimParams`（P 層，預設＝關閉或中性值 1.0，golden 以預設跑＝不變），
> 待 [JCATS-A p.14] 語意的「動態參數」由想定編輯者調整。這讓「加保真」與「不破壞既有局」解耦。

#### WP-C1 壓制與姿態系統（Suppression & Posture）　★★★｜golden：預設中性→不重錄；啟用值另立 golden 案例　✅ 2026-07-31

> ✅ **完成**——worklog `docs/worklog/suppression-posture.md`。既有 3 個 golden **未重錄**
> （中性預設守住），新增 golden 案例 `suppression_defense_60`（並做過 mutation test）。
> **接線做完跑驗收條文第一次就紅，砲兵那條路徑整條漏掉**——三個規格沒點破的缺口：
> ① `AreaFireAdjudicator` **完全沒有累積壓制**（壓制只掛在直射命中，「砲兵用來壓制」的砲兵路徑沒接上）；
> ② `resolve_area_fire` **完全不看姿態**（掘壕與露天傷亡一模一樣——工事最該擋的就是砲擊）；
> ③ **壓制半徑 ≠ 殺傷半徑**（新增 `SUPPRESSION_RADIUS_MULT = 3.0`，逐單位帶「幾發落進它的壓制半徑」）。
> **與規格不同的實作裁決**：衰減率取 **0.7 不是常見的 0.85**（1 tick = 1 分鐘，0.85 要 29 分鐘才清得掉，
> 那讓一次砲擊長得像戰損；壓制是**可逆的**，這是它與戰損最根本的差別）；
> 聚合裁決的壓制/姿態放 `AggregateForce` **不放 `AggregateEnv`**（多方混戰裡每支部隊被壓制的程度不同），
> 且係數**只在非中性時才進 `coefficients`**，否則會改掉既有局每一則事件的序列化內容與 ledger 雜湊鏈。
> **驗收實測**：20 發 155mm 打滿編 120 步兵連 → DUG_IN 傷亡 0.64 / 露天 1.28（剛好一半），
> 落彈當下射擊效能修正 **0.40**。殲滅極慢、壓制顯著，條文成立。
> ⚠ 移交出去的一項：面射擊的**絕對**殺傷量偏低（`area_fire._loss_for` 自標 v0 佔位），已記 Backlog。

**動機**：`EnvSnapshot.shooter_suppression_modifier / target_posture_modifier` 從交戰真實化時代就恆 1.0——
掛點早就留好，系統一直缺席。沒有壓制，砲兵的主要戰術功能（壓制敵火力而非殲滅）無法表現；
沒有姿態，防禦/掘壕的生存優勢無法表現（[JCATS-A p.7,26] 隊形與工事影響受損）。

**規格**：
- `TacticalUnit` 熱狀態增 `suppression: float [0,1]`（0=無壓制）與 `posture: MOVING|HASTY|DEFENSE|DUG_IN`。
- 壓制累積：單位每次被（或近失彈）命中 → `suppression += k_hit`（依武器類別；砲兵高、直射低）；
  每 tick 衰減 `suppression *= decay`。純函數 `adjudication/suppression.py`。
- 壓制效果：`shooter_suppression_modifier = 1 - c * suppression`（射擊效能下降）；
  移動速度 `v_eff *= (1 - c_move * suppression)`（趴下的部隊走不動）。
- 姿態效果：`target_posture_modifier`——MOVING 1.0 / HASTY 0.85 / DEFENSE 0.7 / DUG_IN 0.5（v0 值，全進 SimParams）。
  姿態轉換要時間：HASTY 即時、DEFENSE 30 分鐘、DUG_IN 4 小時（模擬時間，tick 計數），期間算前一級。
  移動即打回 MOVING。POSTURE 令型（現為 NoOp）就此接活。
- AI/前端：單位卡與 COP 顯示壓制條與姿態徽章；AI context 供應己方壓制度（敵方不供應——觀測不到）。

**檔案落點**：`adjudication/suppression.py`（新，純函數）+ `engagement.py`/`aggregate.py` 消費係數 +
`engine/movement.py` 速度折減 + `engine/kernel.py` 每 tick 衰減（進 adjudicator 階段前）+
`orders/`（POSTURE 令橋接）+ 契約（UnitView 增 posture/suppression 欄）。

**驗收**：砲兵對 DUG_IN 步兵連射 5 輪——殲滅極慢但目標射擊效能顯著下降（數值記 worklog）；
壓制在停火後 N tick 內衰減歸零；golden 6 以中性值不變；新增 golden 案例 `suppression-defense` 釘住啟用行為。

#### WP-C2 障礙工事與工兵裁決　★★★｜golden：同上策略　✅ 2026-07-30

> **已完成**（worklog: `docs/worklog/obstacles-engineer.md`）。與規格不同的實作裁決：
> 1. **下面「移動 A* 與交戰完全無視它」的敘述不精確**（開工前查證後修正）：
>    `classify_crossings` + `_apply_forced_attrition`（#28）早就讓「強穿阻礙」付出隨機額外耗損。
>    真正缺的是**型別語意**——對引擎而言，一片雷區與一圈鐵絲網是同一個東西。本卡補的是這一層。
> 2. **BREACH/EMPLACE 收成一個 `ENGINEER` 令型**（同 WP-C3 的理由）：兩者是同一件事——
>    工兵對障礙做工，都要工兵、都要時間、都改同一張 MapFeature。
> 3. **`unit_kind=ENGINEER` 放 `TacticalUnit.attributes` 而不是新欄位**：ORBAT 的兵種屬性
>    （`platform_count` 等）本來就住在那裡，為一個布林開 migration 換不到任何查詢能力。
> 4. **中性做成結構性的**：`typed()` 在入口把沒有 `obstacle_type` 的標註整個濾掉，
>    既有局那條路徑一次幾何判定都不做、**一次 RNG 都不抽**（串流有狀態，多抽一次會位移後續所有結果）。
> 5. **觸雷後令即結束**（COMPLETED，停在原地）而非扣血照走——雷區的價值是把縱隊釘住。
> 6. **未竟**：前端 ENGINEER/FORMATION 下令 UI（後端與契約已通，但**使用者點不到**——
>    V2.1 exit 的 armor-breakthrough CPX 需要破障，這是那張卡的前置）；
>    地圖編輯器選 `obstacle_type`/`density`；ORBAT 勾 `unit_kind`；
>    `blocks_road`（斷橋）尚未接進路由/道路加速；障礙 contact 偵測未做。

**動機**：[JCATS-A p.5–6] 的「公正性」範例一半在講障礙：雷區阻機動、斷橋改道、爆破需合理工時；
[JTLS-F p.1058] 鐵絲網/沙包連結 Class IV 消耗。MATSO 的 `MapFeature(kind=OBSTACLE)` 只是圖——
移動 A* 與交戰完全無視它。這是「畫了雷區敵人照樣開過去」級別的保真斷層。

**規格**：
- MapFeature 增裁決語意：`attributes.obstacle_type: MINEFIELD|WIRE|TANK_DITCH|ABATIS|BRIDGE_DEMO`、
  `attributes.density`（雷區）、`attributes.breached: bool`。**擁有陣營之外不可見**（迷霧照舊），
  但**裁決對所有人生效**——敵軍撞進未偵測雷區正是要的效果。
- 移動整合：`engine/movement.py` 逐 tick 檢查行經格是否落在障礙幾何內（h3 化快取；障礙建立/破障時失效）：
  - MINEFIELD：進入→每公里 `mine_strike_p`（DeterministicRNG "movement"）擲骰，命中→戰損事件＋單位停止＋壓制；
    工兵單位（`unit_kind=ENGINEER`，ORBAT 新欄）通過機率減半。
  - WIRE/TANK_DITCH：非工兵單位速度 × 0.1（實質阻擋）；工兵破障（見下）。
  - BRIDGE_DEMO：該河段 road 加速失效＋涉水判定（複用地形 terrain_class WATER 語意）。
- 工兵作業：新令 `BREACH {feature_id}`——工兵單位到場後依 `breach_time_ticks`（obstacle_type × SimParams）
  作業，完成→`breached=true`（裁決失效、圖示改）＋記事件。構工：`EMPLACE {obstacle_type, geometry}`
  同理需工時（[JCATS-A p.13] 工事構築須符合實際工時），消耗 Class IV（WP-C7 後接帳）。
- 偵測：工兵偵察（或任一單位近距離）可將敵障礙轉為本軍 contact 型標註（`intel` 擴充：obstacle contact）。

**驗收**：未偵測雷區使進攻縱隊觸雷停擺；工兵先行破障後同路線通過無損；
BREACH 中斷（工兵被打掉）障礙保持有效；AAR 顯示觸雷/破障事件鏈。

#### WP-C3 乘駐車與隊形　★★｜golden：同上策略　✅ 2026-07-31

> **已完成**（worklog: `docs/worklog/mounted-formation.md`）。與規格不同的實作裁決：
> 1. **MOUNT/DISMOUNT 收成一個 `FORMATION` 令型**（payload 可帶 `formation` 與/或 `mounted`）：
>    三個令型會讓席位表、payload 表、預檢分派、前端下拉各多兩個分支，而它們表達的是同一件事
>    ——宣告本單位要以什麼狀態行動（與 POSTURE 同類）。
> 2. **`mounted` 是三態（`bool | None`）而非布林**。第一版用 `bool(state.get(...))`，
>    缺鍵被收成 False＝「已下車」＝吃 0.8 受彈面折減，**既有局命中率無聲下降 20%**。
>    golden 抓不到（無直射交戰案例）、交戰單元測試也抓不到（直接建 `EnvSnapshot`）——
>    錯在**接線層**。已修（`9e10f58`）並在該層補測試。
> 3. **未竟**：前端隊形/乘駐車切換 UI 與 2525 mounted 修飾符；`mounted` 影響移動速度
>    （需改 `UnitMobility` 解析，陷阱：預設不可讓載具單位變成用走的）；
>    載具毀損→乘員傷亡折算待 #48 單位編成組成。

**動機**：[JCATS-A p.12,25]（Mount 是操作要點：單兵未上車行軍速率過慢）、p.7,26（五種隊形影響受損與火力發揚）。

**規格**：
- `mounted: bool` 熱狀態欄：MOUNT/DISMOUNT 令（或 MISSION 分解自動插入）。mounted 時：
  速度＝載具 profile（現行 per-unit 機動已支援，缺的是狀態切換）；被命中時傷亡走「車輛毀損→
  乘員傷亡折算」（[JTLS-F p.1058]：載具毀損自動折算車組/載員，係數進 SimParams）。
  dismounted：速度＝FOOT、受彈面小（target modifier × 0.8）。
- `formation: COLUMN|LINE|WEDGE|VEE|HERRINGBONE`：COLUMN 行軍快、遭砲擊受損高；
  LINE 火力發揚全額、機動慢——實作為兩個係數表（march_speed_mult / volley_exposure_mult / fire_frontage_mult），
  值全進 SimParams。前端單位卡可切換，MISSION 分解自動選（行軍段 COLUMN、接敵段 LINE/WEDGE）。

**驗收**：機步連 mounted 行軍速度≈載具速度、dismounted≈4–5 km/h；COLUMN 遭砲擊傷亡 > LINE（同 seed 對照）；
2525 符號加 mounted 修飾顯示。

#### WP-C4 環境演進：逐 tick 天氣、晝夜與照明、煙幕　★★｜golden：重錄（天氣快照語意變更）　🟡 C4a ✅ 2026-07-30（晝夜）；C4b/C4c 未做

> **C4a（晝夜與照明）✅ 已完成**（worklog: `docs/worklog/daylight.md`）。與規格不同的裁決：
> 1. **這一段 golden 不必重錄**——「重錄」是針對 C4b 的天氣快照語意變更。晝夜只要中性
>    預設守住（未宣告 → 光照恆 DAY → 三個係數全 1.0）就不必，與 WP-C1/C3 同一招。已實測 8 個 golden 未動。
> 2. **日出日落是想定參數而非天文計算**：兵推要的是「白天打還是晚上打」，
>    想定作者給兩個時刻比一個他無法覆寫的公式有用（夜訓本來就會挑時間）。
> 3. **`night_capable` 掛裝備不掛單位**：掛單位的話一個連配一支夜視鏡就整連免罰，
>    而那正是夜戰最關鍵的差別。感測器與行軍是兩個獨立旗標。
> 4. **「我看多遠」與「我多好被看到」拆成兩個軸**：前者吃夜視，後者是環境（對雙方成立）。
>    合成一個數字會讓「我方有夜視」同時變成「敵人比較容易看見我」。
> 5. **未竟**：照明彈（ILLUM，需要局部短暫的光照覆寫實體）、前端晝夜呈現、C4b、C4c。

**動機**：天氣是 session 啟動單一快照（README §5 盤點）；[JCATS-A p.7] 晝夜與人工照明影響運動/偵測；
煙幕（化學兵標準配屬，p.19）阻視線。SimClock 已有時刻，這是低掛果實。

**規格**：
- **天氣刷新**：`sim_runtime` 每 `weather_refresh_ticks`（SimParams，預設一模擬小時）重拉 weather 快照
  （SYNTHETIC 模式下由 seed 派生演進，決定性；LIVE 模式僅限非重播局）。`WeatherMode.REPLAY` 一併補上：
  Ledger 記每次快照內容，重播時照放（修掉 README §9 盤點的 REPLAY 缺口）。
- **晝夜**：由 SimClock 模擬時刻導出 `light_level: DAY|DUSK|NIGHT`（想定給日出日落參數）。
  效果：NIGHT 時 EO_DAY 型感測距離 × 0.3（有夜視裝備的單位不受罰——EquipmentTemplate 增 `night_capable`）、
  移動 v_eff × 0.8（無夜視）。照明彈：`ILLUM` 火力任務（WP-C10）在目標區產生臨時 DAY 圈。
- **煙幕**：`SMOKE` 彈種/發煙任務 → 生成 `SmokeCloud`（中心、半徑、剩餘 tick，風向漂移＝weather wind 向量）。
  LOS 判定疊加：`make_engage_env`/`make_detect_env` 在 terrain LOS 之後檢查視線段是否穿越活躍煙幕
  （幾何純函數，`adjudication/obscurants.py`）。煙幕是**雙面的**：擋敵也擋我。
- 熱狀態/廣播：煙幕作為短暫實體進 STATE_DIFF（前端畫半透明圓，隨風漂移動畫）。

**驗收**：夜戰對照局（同 seed、僅時刻不同）偵測接觸數顯著下降；煙幕投放後跨幕交戰 LOS 被擋、
風把煙吹離後恢復；REPLAY 模式重播含天氣變化的局位元一致。

#### WP-C5 通聯後果閉環：位置凍結與敵情粗化　★★｜golden：不動（投影層變更）　✅ 2026-07-30

> **已完成**（worklog: `docs/worklog/comms-consequences.md`）。與規格不同的實作裁決：
> 1. **規格四項之外先修了一個紅線 3 違反**：STATE_DIFF 的信封**從來沒有陣營受眾標籤**，
>    `is_visible` 對所有人回 True → 敵軍即時座標一直廣播給每個連線的 client。
>    這不是順手修——一個廣播給所有人的信封沒有「己方視角」可言，不先做每陣營投影就做不了凍結。
>    做法：每 tick 發 N+1 份信封（每陣營一份已投影的 + 一份 `factions:[]` 的真實副本），
>    並新增 `exclusive` 受眾語義關掉全知旁通（否則統裁會同時收到 N 份互相矛盾的副本）。
> 2. **敵情粗化只能做到陣營層**（規格原文亦如此）：`IntelContact` 沒有觀測者單位欄位，
>    做不到「該筆情報的回報者斷聯 → 該筆凍結」。故 `IntelGranularity.FROZEN` 目前與 COARSE
>    的投影效果相同（量化 + 降級），差別只在 `comms_posture` 顯示的字。已記 backlog。
> 3. **凍結的是視野不是單位**：新增 `report_lat/lng/tick` 熱狀態欄位，真實 `lat`/`lng` 照常演進。
>    直接凍住熱狀態座標會連射程/LOS 裁決一起騙到。
> 4. 白軍**指定 `as_faction`** 時凍結與粗化照套（那是在問「這一軍看得到什麼」）；
>    驗收條文的「白軍視角照動」指的是未指定視角的 god view。

**動機**：SPEC_FULL §6.2 的 MUST（通訊狀態的戰術後果）只做了一半：`order_admissible`（斷聯不受新令）有了，
`intel_granularity`（DEGRADED→敵情粗化）與 `position_report_*`（斷聯單位在己方 COP 位置凍結）
已定義但無消費者（README §6 盤點）。

**規格**：
- **位置凍結**：`GET /units` 與 STATE_DIFF 的**己方視角**投影中，OFFLINE 單位回報最後 ONLINE 時的位置與狀態
  （加 `stale_since_tick` 欄位）；白軍/全知視角照舊真實位置。實作於 API/broadcast 投影層（不動熱狀態——
  真實位置照常演進，只是「指揮所看不到」）。前端把 stale 單位畫半透明＋時間戳。
- **敵情粗化**：本陣營 comms 整體 DEGRADED 時，`GET /intel` 的 contact 位置量化到 h3 res-6（約 3km 格心）、
  fidelity 上限 DETECTED。實作於 intel 投影（同一原則：資料不動、投影降級）。
- AI 一致性：WP-A1 的 AI context 走同一投影（AI 指揮官同樣看到凍結/粗化——迷霧一致性）。

**驗收**：拔掉單位通訊裝備（或走進 NLOS 山谷）→ 己方 COP 該單位凍結、白軍視角照動；
DEGRADED 陣營的敵情圖示跳格；恢復 ONLINE 後投影即時回真。

#### WP-C6 交戰引擎收尾：多方混戰、聚合門檻、目標編成（#48）　★★｜golden：重錄

三件盤點欠帳合一卡群：
1. `resolve_multiway_tick`（N 方同格混戰）接進 `adjudicator`（現只走成對）；測試：三陣營互為 HOSTILE 同格。
2. `should_aggregate` 讀想定 `aggregate_adjudication_level`（現寫死 BATTALION）。
3. #48：combined 裁決把目標視為編成組成（armor/infantry/soft 比例，由目標裝備清單導出）——
   AP 彈打步兵、HE 打裝甲的錯配懲罰；多目標火力分配（一單位多武器分打多目標，fire_policy 擴充）。
聚合係數（`_AGG_*` 佔位值）一併搬進 SimParams 並以 [INDSR p.21–22] 的方法論做一次校準實驗
（蒙地卡羅 30 次、對照歷史交換比合理區間，結果記 `docs/worklog/`）——**校準依賴 WP-D1 先行**。

#### WP-C7 後勤體系化：補給類別、消耗、修復、整補　★★★｜golden：重錄

**動機**：[JTLS-F p.1058] Class I–X 與再訂購水位；[JCATS-A p.26–27]「絕非申請後直接恢復戰力」——
補給/修復/人員補充需合理作業時間、於後方恢復再前送。MATSO 只有油料＋彈藥撥交。

**規格**（分 3 卡）：
1. **補給類別最小集**：V2 先做 Class I（口糧/水）、III（油料，已有）、V（彈藥，已有）、IX（維修件）。
   單位熱狀態增 `supply: {I: float, IX: float}`；Class I 每模擬日消耗（斷糧→效能懲罰＋士氣接口預留）；
   補給單位 capacity 按類別分艙（EquipmentTemplate LOGISTICS 的 capacity 結構擴充，契約先行）。
2. **再訂購水位與補給線**：單位低於 reorder level → 自動生成 RESUPPLY_VOUCHER（接 WP-B5 審批或自動核准，
   想定開關）；補給車自動往返（MISSION `MOVE_MARCH` 複用）於補給點↔前線；補給點（新 MapFeature 語意
   `SUPPLY_POINT`，帶庫存）被敵佔領/摧毀→下游斷補。**這讓「打擊敵後勤」成為可行戰法**。
3. **修復與人員補充**：戰損單位退至補給點半徑內 → 消耗 Class IX + 修復時間 → 恢復裝備數；
   人員補充速率（人/模擬日）於想定定義；**前線不整補**（距敵 contact X km 內不觸發），落實
   [JCATS-A p.27]「後方恢復戰力再前送」。
**驗收**：斷補的裝甲連 3 模擬日後（油盡/彈盡/口糧盡）效能階梯下降；打掉補給點後下游單位水位不再回升；
修復中的單位遭襲即中斷整補。

#### WP-C8 多解析度建模（MRM）：聚合↔解聚　★★｜golden：重錄（僅新路徑）

**動機**：[JTLS-F p.1056–1058] 給了完整工程解法：解聚合/再聚合、單位階層維護、實數↔整數轉換、
毀損事件同步、防殭屍物件。MATSO 的兩種裁決（實體 volley vs 聚合 Lanchester）並存但單位粒度固定。

**規格**（V2.1，依賴 C7 帳目）：營級單位可 `DISAGGREGATE` 為其 ORBAT 子單位（連/排）——
戰力/彈藥/油料按比例分帳（實數→整數用最大餘數法，決定性）；子單位獨立機動/交戰；
`REAGGREGATE` 時帳目回收合併（陣亡不復活——帳目守恆測試釘住）。觸發：手動令＋自動
（進入敵 contact X km 內自動解聚、脫離後自動再聚，想定開關）。h3 佔格與 COP 符號隨層級切換。
**驗收**：解聚→交戰→再聚 全程 personnel/彈藥守恆（Σ子單位＝母單位±戰損）；AAR 事件鏈完整。

#### WP-C9 友軍誤傷語意修正　★｜golden：不動（預設關）　✅ 2026-07-30（後端；前端 affordance 未做）

> **已完成**（worklog: `docs/worklog/fratricide.md`）。開工前派 6 個平行 scout 掃現況，
> 掃出來的東西改變了這張卡的形狀：
> 1. **「關」的那一邊本來就是破的**（先切 C9a 修掉，commit `9778772`）：`api/deps.py`
>    ——人類指揮官下的每一道令走的那一條——**從來沒有注入 `relations`**，`run_precheck`
>    於是退回全 HOSTILE 預設，而 `is_hostile("BLUE","GREEN")` 在那份矩陣裡是 True。
>    「不可打盟軍」只擋得住打自己陣營；AI 路徑反而擋得住。**恰好倒過來**。
> 2. **三條路徑本來就不對稱**：`FIRE_MISSION` 完全沒有陣營檢查（打自己人今天就有真實傷亡）、
>    `MISSION` 只透過 ENGAGE 子令間接受擋。只接進 ENGAGE 預檢會做出「開了才會誤傷」的假象。
> 3. **面射擊刻意不受開關影響**——砲彈不挑陣營。開關管的是「故意瞄準友軍」，
>    不是「砲彈落在友軍身上」。
> 4. **開關不涵蓋 NEUTRAL**：原分支 `not is_hostile(...)` 同時涵蓋自己陣營/ALLIED/NEUTRAL，
>    照規格直接套上去會無聲地把「攻擊中立方」一起放行。
> 5. **又一個真的 bug**：`friendly_losses` 用 `faction == shooter_faction` 字串比較 →
>    聯軍誤傷（BLUE 打到 GREEN 盟軍）不被標成友軍傷亡，AAR 上看起來像正常戰果。已改走關係矩陣。
> 6. **`FRATRICIDE` 一個受害者一筆**，不是一筆總結：`event_audience` 在兩個 unit id 都推不出
>    陣營時回 `None`＝**全域廣播**，總結型事件會把「對面在自相殘殺」直接播給敵軍。
> 7. **未竟**：前端仍是一道獨立的鎖（COP 濾掉盟軍、拒絕點友軍為目標）；AI 結構上仍不可能誤傷
>    （LLM 看到敵情前就被 `is_hostile` 濾過）；**直射濺射未做**——那是新能力不是新係數
>    （`Target`/`EnvSnapshot` 沒有 lat/lng，且 `lethal_radius_m` 只在武器契約的 artillery
>    `$def`，KINETIC 範本一律 0.0）。

[JCATS-A p.5–6]：成熟系統「命令照輸入執行、後果照裁定」——錯誤火力計畫打到自己補給點照裁。
現況 precheck 直接擋友軍目標。**規格**：想定開關 `allow_fratricide`（訓練嚴格模式開）；
開啟時 precheck 對友軍目標改為**強警告＋須 override**（人類）或 escalate G6（AI），
執行後照常裁決＋特別事件 `FRATRICIDE` 供 AAR/評量重點標記。區域武器（砲兵 HE）的濺射
本就該傷及半徑內友軍——`resolve_combined_engagement` 增加濺射判定（同格/鄰格友軍按距離衰減承傷）。

#### WP-C10 計畫火力與 call-for-fire 作業鏈　★★｜golden：不動（新路徑）

> **C10.1（臨機火力申請）✅ 已完成（2026-07-30）**——worklog `docs/worklog/call-for-fire.md`。
> `CALL_FOR_FIRE` 申請種類 + **觀測條件**（申請者陣營須有單位對目標有 LOS，
> 共用交戰預檢的同一個 `PhysicsGateway`）+ 重用 B5.2 的審批鏈。
> ⚠ 與 `FIRE_SUPPORT` **刻意分開**：後者是「解鎖一次曲射任務」的授權（掛在 ENGAGE 令上），
> 前者是「我看到目標、請對這裡射擊」的任務單。合併會讓「有權開火」與「有目標可打」混為一談。
> **本卡只做到申請受理**——核准後自動生成火力令、指派砲兵單位屬後續卡。
>
> **C10.2（面目標射擊）✅ 已完成（2026-07-31）**——worklog `docs/worklog/area-fire.md`。
> 原訂 C10.2 是 FirePlan/排程，動手前發現**規格沒點破的前提缺口**：火力計畫的目標是
> **座標**，但引擎的 `ENGAGE` 一律要 `target_unit_id`——FirePlan 不是缺一層排程包裝，
> 是缺一個能力。故先補 `OrderType.FIRE_MISSION` + `adjudication/area_fire`
> （CEP→Rayleigh 落點、齊射逐發抽、殺傷半徑內**敵我皆傷**）+ 引擎接線 + COP 下令，
> 排程順延一號。**編號因此重排**：
>
> | 卡 | 範圍 | 狀態 |
> |----|------|------|
> | C10.1 | 臨機火力申請（CALL_FOR_FIRE + 觀測條件） | ✅ |
> | C10.2 | 面目標射擊（打座標，FirePlan 的前置能力） | ✅ |
> | C10.3 | `FirePlan` 實體 + at_tick/on_call 排程 | ✅ |
> | C10.4a | 觀測判定 + 散布掛觀測者 + 關掉 damage 洩漏 | ✅ |
> | C10.4b | `BDA_REPORT`（帶迷霧誤差）+ feed 呈現 | ✅ |
> | C10.5 | 陣地變換 `survivability_move` | ✅ |
>
> **WP-C10 五張子卡全數結案（2026-07-31）。**

**動機**：[JCATS-F p.12–13] 整個第肆章：目標獲得→觀測所→接敵報告→臨機火力申請→會商→射指所執行→
效果回報（BDA）；計畫火力（預置目標、定時射擊、攻擊準備射擊 20 分鐘）；[JCATS-A p.24,26] 火力任務範本、
射界管理、陣地變換。

**規格**：
- **火力計畫實體** `FirePlan`：`{targets: [{id, latlng, ammo_type, rounds, schedule: at_tick|on_call}], status}`
  ——COP 可建；`at_tick` 到時自動下 FIRE_MISSION 令；`on_call` 由 FSO 席位一鍵呼叫。
  ⚠ **原文寫「由 MSEL 執行器（WP-B2）複用」，那不成立**（C10.3 實作時查證）：
  `TriggerChecker.check()` 回的是 `list[LedgerEvent]`，結構上產生不了令；trigger 槽又在
  `run_tick` 的最後才跑（drain 在最前面）故必定慢一 tick；而且活執行期傳的是
  `NoOpTriggerChecker`，MSEL 從未運行。實作改走 `run_paced(pre_tick=…)`——它在
  `run_tick` 之前，落庫的令當個 tick 就被 drain 撿走。
- **臨機火力鏈**（依賴 WP-B5 席位）：觀測單位（有 LOS 的任一友軍）對 contact 發 CALL_FOR_FIRE request →
  FSO 核准（或想定設自動）→ 就近可達砲兵單位執行 → 落彈後觀測單位自動回 BDA 報告
  （觀測到的目標狀態，帶迷霧誤差——BDA 是情報不是 ground truth）。
- **陣地變換**：砲兵射擊 N 輪後 `survivability_move`（自動位移 1–2km，想定開關）——反砲兵雷達（遠期）預留。
**驗收**：預劃 H-20 分攻擊準備射擊自動執行；前觀死亡後 on-call 任務失去觀測修正（散布加倍——
`volley dispersion` 係數掛 BDA 觀測存在與否）；全鏈事件可在 AAR 重建時序（→ WP-F5 評量原料）。

<a id="wp-d"></a>
### WP-D：分析與決策支援

> 本群把 MATSO 從「跑一局看結果」升級為 [INDSR] 展示的分析方法論：批次→統計→歸因→修模。
> 不變量 5：全部跑在 `purpose=ANALYSIS` 的複製 session，絕不碰進行中的演習局。

#### WP-D1 蒙地卡羅批次實驗引擎　★★★｜golden：不動（分析路徑獨立）

**動機**：[INDSR] 每個個案都是「固定想定 × 30–50 次重複 × 統計分布」（p.21,56,70,88）——
這是分析型兵推的最低門檻。MATSO 的決定性引擎讓這件事**異常便宜**：同一想定換 seed 即是一次抽樣，
不需要任何新物理。

**規格**：
- 新實體 `Experiment`：`{id, name, base_scenario|base_session(clone 起點), variations, replications, seeds[],
  status, results}`。`variations`＝參數掃描維度（[INDSR p.55] 齊射量×威脅軸的因子矩陣）：
  每維指定「路徑→值列表」（路徑可指向：SimParams 欄位、ORBAT 單位數量/位置、裝備模板 baseStats 欄），
  笛卡兒積展開為 case 矩陣，每 case × replications 個 seeds。
- **批次執行器** `core/app/analysis/runner.py`：case 佇列 → 逐一 clone session（`purpose=ANALYSIS`、
  headless：無 WS 廣播、無 AI 心跳牆鐘（AI 決策若參與，走同步決定性 stub 或錄放，V2.0 先限「無 LLM 局」；
  LLM 參與的批次實驗＝V2.1，需 O11.6 錄放機制擴充）→ 以最大速率跑至勝負/時限 → 蒐集 MOE（WP-D2）→
  刪除或保留 session（保留 N 個代表性案例供回放歸因，[INDSR p.57] 的回放發現 CEC 效應正是這樣來的）。
- 並行：多 session runner 本就隔離，批次以 `asyncio.Semaphore(k)` 限流；進度經 WS 推 `EXPERIMENT_PROGRESS`。
- 前端：新頁 `analysis/`——實驗設定（想定選擇、掃描維度表格、重複數）、進度、結果表。

**驗收**：「藍軍砲兵 2/4/6 連 × 30 seeds」實驗一鍵跑完，產出 90 局的勝率/戰損分布；
同 seed 同 case 重跑結果位元一致（決定性驗證）；實驗中不影響同機上進行中的演習局（tick 節奏監測）。

#### WP-D2 MOE 框架與成本效益指標　★★★｜golden：不動

**動機**：[INDSR p.19–20] 的三層指標：體系層 MER（損失×**獲得成本**加權交換比）、系統層 DR（摧毀比率）、
技術層 KR/MR（每發命中/未中），與 hit/kill/destroy 狀態機；p.56 的「先定 MOE 再設計實驗、
刻意隔離無關變數」紀律。MATSO 勝負 DSL 只裁勝負，AAR 統計連命中率的帳都對不平（README §5 盤點）。

**規格**：
- **MOE 定義語言**：`Experiment.moes: [{id, name, expr}]`——expr 是受限表達式（同 DSL 紀律禁 eval），
  可引用的原子量：`losses(faction, class?)`、`kills(faction)`、`shots/hits(weapon_class)`、
  `cost(unit|equipment)`（EquipmentTemplate 增 `unit_cost` 欄——[INDSR p.21] 成本入模）、
  `ticks_to(condition)`、`survived(unit_id)`。內建範本：exchange_ratio、MER、DR、KR、完全攔截率。
- **hit/kill/destroy 對帳修正**：Ledger 交戰事件已有 damage 細節；補「聚合交戰雙側戰損入帳」
  （修 README §5 盤點的 initiator loss 不入帳問題）與 mission-kill vs destroy 區分
  （效能<30%＝mission kill，[INDSR p.20] 的 kill 語意；destroy＝personnel/裝備歸零）。
- AAR 頁與實驗結果共用 MOE 計算器（單局也能看 MER/KR）。

**驗收**：對 [INDSR p.21] 的雷射案例做一次複刻式實驗（自建近似想定）：MER/DR/KR 三層指標
自動產出、與手算一致；聚合交戰的雙側戰損帳目守恆測試。

#### WP-D3 態勢分析圖層與自動戰術線　★★｜golden：不動（唯讀分析）

**動機**：[IST160 p.15–17,20]：全軍感知覆蓋聯集、直射火力覆蓋聯集、局部戰力比分區熱圖、
FLOT/FEBA 自動生成——既是參謀 SA 工具，也是餵 LLM 的結構化特徵（讓 AI 讀「此帶劣勢」而非原始單位表）。

**規格**：
- 新 API `GET /sessions/{id}/analysis/layers?as_faction=`（後端計算、迷霧過濾）：
  - `sensor_union`：本軍各單位感測範圍（含地形裁切 viewshed 快取）的聯集多邊形——「我看得到哪裡」；
    反向 `sensor_gaps`（作戰區內未覆蓋帶）。
  - `fire_union`：直射/曲射火力涵蓋聯集——「我打得到哪裡」。
  - `force_ratio_grid`：h3 res-7 分格戰力比（本軍 vs 已知敵 contact 的戰力點，[IST160 p.16] 綠黃紅）——
    **只用該陣營可見情報算**（迷霧一致性：這是「認知的戰力比」不是真值；白軍視角另有真值版）。
  - `feba`：由敵我最前緣 contact/單位推 FEBA 折線（凸包裁剪＋平滑，v0 簡化演算法即可，
    [IST160 p.20] 亦僅初步成果）。
- 前端：圖層小工具增「分析圖層」組（聯集半透明面、戰力比熱圖、FEBA 線）；30s 或手動刷新（非即時，
  計算成本控制）。
- AI 接口：WP-A1 的 context 增 `analysis_summary`（各帶戰力比的文字摘要，由同一 API 導出）——
  [IST160 p.11]「對的資訊而非所有資訊」。

**驗收**：雙連對峙想定中 FEBA 線落在兩軍之間；遮蔽山谷顯示為 sensor gap；
BLUE 視角的戰力比不含未偵測的 RED 單位（與白軍真值版對照可見差異——這個差異本身就是教學素材）。

#### WP-D4 What-if 分支推演　★★★｜golden：不動

**動機**：[IST160 p.19]：從當前態勢複製、改 ORBAT/位置/敵情、操控雙方快跑替代方案——參謀的
「如果我把預備隊投到左翼呢？」。MATSO 的 clone 只能從想定起點複製；決定性引擎＋熱狀態快照其實已備。

**規格**：
- `POST /sessions/{id}/branch`：從**當前 tick** 快照複製（熱狀態全量 + DB 單位/彈藥/障礙 + RNG 沿用
  master_seed 但 branch 附加 stream 鹽——分支間互相獨立且各自決定性）。分支 session 標記
  `purpose=ANALYSIS, branched_from: {session, tick}`。
- 分支編輯：白軍/分析者可在分支上改單位位置/編成/敵情（等同地圖狀態編輯，已有機制）後放行快跑
  （最大速率、可掛 AI 或腳本化行為）。
- 比較視圖：前端 `branch-compare`——同 tick 對齊的雙欄 COP 快照 + MOE 對比表（WP-D2 計算器複用）。
- 依賴：E1 的活 session checkpoint（分支點快照機制與其共用實作——**先做 E1**）。

**驗收**：進行中演習於 T=1000 開兩個分支（預備隊投左/投右）、各快跑 500 tick、
MOE 對比表產出；原局完全不受影響（tick 節奏、狀態雜湊前後一致）。

#### WP-D5 時間維度可行性與持續力分析　★★｜golden：不動

**動機**：[IST160 p.14,21] 任務可行性含時間（「趕不趕得及」）是六個參謀組共同需求——覆蓋面最廣的單一功能；
p.18 補給支援「還能撐多久」雙向計算。MATSO 已有 ETA（移動預覽）與消耗率資料，缺彙整層。

**規格**：
- 下令面板的 MOVE/MISSION 預覽增：抵達時刻（模擬時間）＋「若須 H 時前到達」檢查（時限由令攜帶
  `deadline_tick?`，逾期預測→琥珀警告；正式逾期→事件，接 WP-F5 評分）。
- 新 API `GET /sessions/{id}/analysis/sustainment?as_faction=`：逐單位「油料/彈藥/口糧以當前消耗率
  可撐 tick 數」、補給單位「涵蓋哪些單位（距離＋可達）、庫存分配後各能再撐多久」——
  [IST160 p.18] 的雙向計算。COP 單位卡顯示「續戰力 N 小時」徽章；低於閾值觸發 WP-D7 警告。

#### WP-D6 AAR 成熟化：地圖重播、對帳、匯出　★★｜golden：不動

> **D6.1 ✅ 已完成（2026-07-30）**——worklog `docs/worklog/aar-map-replay.md`。
> 規格把這項描述成「接視覺」，實際是 `reconstruct_states` **寫好後從未被 API 接線**，
> 底下累積了 5 個沒人踩過的錯：①戰力點被當成效能%；②聚合 `damage_calc`（雙方損失和）
> 從守方單側扣（即本表第 23 列的「聚合戰損歸帳單側」，提前在此修掉）；
> ③位置只讀 `ai_decision`，但移動事件的 lat/lng 全在 `detail`（重播中所有單位都不動）；
> ④帳本依 seq 排、tick 非單調（實測首筆即 tick 3700），原本的 `break` 會直接回空狀態；
> ⑤記錄的權威 health 被導出值覆寫。全部有測試釘住。
> 新增端點 `GET /sessions/{id}/aar/replay/states`（契約先行），形狀為靜態底本 + 逐 tick 差異。
> ⚠ **tick 0 基準位置是近似值**：帳本沒有部署事件，白軍「地圖狀態編輯」也不落帳，
> 故取該單位最早一筆有座標的事件（誤差最多一個移動步長）；從沒動過的單位取 DB 現值（精確）。

三件盤點欠帳＋文獻要求合併：
1. **地圖重播**：AAR 的 `scrubTick` 滑桿驅動 MapCanvas 重繪（`replay.frames` 已有資料未接視覺——
   README §8 盤點）；播放/暫停/倍速、事件書籤跳轉、[JCATS-F p.6]「定位到任一時間點回放檢討」。
   重播用 `reconstruct_states` 需先修 README §5 盤點的量綱混用（health% vs 戰力點）。
2. **統計對帳**：聚合交戰雙側入帳（隨 WP-D2）；命中率分母語意修正。
3. **匯出管線**：[INDSR p.43–44] 的 CSV/SQL 匯出——`GET /sessions/{id}/export?format=csv&tables=events,units,shots`
   （串流分頁，修「全量載入記憶體」盤點項）；AAR bundle（JSON+CSV+想定包）供外部工具（pandas/Excel）分析。
   Lua 式內嵌腳本**不做**（Non-Goal §8——匯出到外部生態系即可）。

#### WP-D7 情境化警告與報告分級　★｜golden：不動

[IST160 p.14,21]：彈藥低於閾值、支援中斷倒數、時限風險→主動警告而非事件流平鋪。
**規格**：規則式警告引擎（後端，per-faction 評估：`ammo_below/fuel_below/deadline_risk/support_lost/
contact_new`）→ WS 推 `ADVISORY` 事件（受眾＝該陣營）→ 前端警告中心（分級 INFO/WARN/CRITICAL、
可靜音、有讀/未讀）。規則閾值進 SimParams（H 層可熱調）。**不用 LLM**——這層是確定性規則，
LLM 參謀摘要屬 WP-F 範疇。

<a id="wp-e"></a>
### WP-E：工程韌性與維運

#### ✅ WP-E1 活 session checkpoint 與崩潰復原　★★｜golden：不動（新增快照不影響行為）

> **✅ 已完成（2026-07-29）**——worklog `docs/worklog/live-checkpoint.md`、**ADR 007**。
> 規格四項外另修三個未列斷點，最嚴重者：`SimClock` 每次 runner 重建都回 tick 0（**不需崩潰，每次重啟都發生**）。
> ⚠ 規格要求的「Ledger 尾段截斷」**未採用**：實體刪除會讓 hash chain 的防竄改性歸零，
> 改採**邏輯截斷**（ROLLBACK 事件記被棄 seq 區間，AAR 據此濾除）——完整論證見 ADR 007。
> 已知界線：RNG 只還原到**快照當下**（快照後消耗的抽樣次數無處可考），最多倒退一個間隔。


盤點事實：`sim_runtime` 建 Kernel 未傳 `checkpointer`——活局不落快照；RNG 狀態不序列化，
mid-interval 崩潰只能回到 checkpoint 重放。**規格**：(1) runner 組裝時掛 checkpointer
（間隔 SimParams，預設 5 分鐘牆鐘）；(2) `DeterministicRNG.get_state/set_state`（numpy PCG64
`bit_generator.state` 序列化——PROGRESS 既有 backlog O1.6）；(3) checkpoint 內容擴充：
熱狀態＋RNG state＋pending orders＋MSEL 已觸發集；(4) core 重啟時掃描未收場 session → 自動 recover
＋前滾（自 checkpoint 起 replay Ledger 尾段）→ 恢復 runner；(5) ROLLBACK 端點接活（白軍回滾到
書籤 tick——B2 的 PAUSE + 快照定位）。**驗收**：kill -9 core 後重啟，進行中的局在 ≤1 快照間隔內
自動恢復且狀態雜湊與崩潰前一致；rollback 後 Ledger 尾段正確截斷（hash chain 重錨定，設計須記 ADR）。

#### WP-E2 認證強化　★★

盤點欠帳打包：refresh token 旋轉＋撤銷表（logout 生效）；帳號鎖定（N 次失敗鎖 M 分鐘，防爆破）；
`needs_rehash` 順手升級接線；JWT secret 生產模式強制。全屬 `core/app/auth/`，測試齊備即收。

#### ✅ WP-E3 `/state` 快照端點與 RESYNC 閉環　★

> **✅ 已完成（2026-07-29）**——worklog `docs/worklog/state-snapshot.md`。
> 關鍵設計：快照**呼叫既有 handler**而非重寫過濾——三個端點的過濾規則本來就不一致
> （units 含盟軍、map-features 不含、intel 以 participant.role 判全知），另寫一份必然漂移，
> 而**迷霧過濾的漂移就是資安漏洞**。為此順帶把 `/intel` 對齊（非參與者的全知者不再 403）。
> `last_seq` **必須在讀狀態之前**取樣，否則兩次讀取之間的 diff 會既不在快照裡又被 client 丟棄。


ws_protocol 的 RESYNC_REQUIRED 契約補全：`GET /sessions/{id}/state?as_faction=` 回單一原子快照
（units＋contacts＋map features＋sim time＋最後 event seq，後端迷霧過濾）；前端 `sessionStream`
收 RESYNC 後以快照原子重建（去掉「週期重抓兜底」的 race）。契約先行：core_api.yaml 定義 StateSnapshotView。

#### WP-E4 監控落地　★★

prometheus/grafana 空殼補實：core 暴露 `/metrics`（tick 時長分布、overrun 率、WS 扇出量、
LLM 心跳延遲、guardrail 攔截計數、DB/Redis 延遲）；compose 增 prometheus+grafana 服務與預置
dashboard（tick 健康、AI 健康、演習總覽）；告警規則（tick p99 超限、AI 全滅、Redis 斷線）。
[JCATS-A p.12] 的飽和測試需要這些儀表才有意義（E5 依賴 E4）。

#### WP-E5 負載測試與 LOD 降載　★

`ops/tools/loadgen.py`：合成 N 單位想定產生器＋壓測 runner（等同 [JCATS-A p.12] 飽和測試×2 的
機器化）；記錄單位數 vs tick 時長曲線，找出當前引擎的容量邊界（60,000 實體 [JCATS-F p.5] 是
長期參照，不是 V2 目標）。LOD 降載：超載時 COP 前端聚合顯示（連→營符號聚簇）——顯示層級調整
[JCATS-A p.12]，引擎不變。

<a id="wp-f"></a>
### WP-F：AI 深化（RAG／評測／MoA）

> 前提認知（memory: rag-data-reality）：**語料與 eval 長期不足是設計前提**，不是待修 bug。
> F 群的排序邏輯：先把「管線的真」補上（F1 真嵌入、F3 稽核），語料規模（F2）隨使用者供檔漸進，
> MoA（F4）等模型與語料到位才有意義。

#### WP-F1 SPEC_INGEST 實作：文檔→RAG 語料管線　★★★

SPEC_INGEST（O9 群任務卡）設計已定、實作未動。V2 起動最小切片：
1. `bge-m3` 真嵌入器接入（`ai/rag/embedder.py` 的 `load_bge_m3()` 落地；模型檔屬部署資產，
   air-gapped 下離線載入；無模型時降級 hash 並在 UI 標示「檢索品質降級」）。
2. ingest CLI 端到端：PDF/DOCX → parse（既有 parse.py）→ chunk → embed → Qdrant upsert →
   `corpus_manifest`（來源/版本/授權留痕）。OCR fallback 維持惰性降級（tesseract 屬部署資產）。
3. 檢索評測：ingest 每批附 5–10 條 QA 對（人工或 LLM 草擬人工定稿）→ `ai/evals` 增 retrieval
   hit-rate 指標——**語料品質從第一天就被量測**。
G5（引用查核）與 `citation_verifier` 注入（盤點：現況 None→AI_BARE 語義）在語料非空後接活。

#### WP-F2 語料與評測擴充　★★

- eval 案例從 3 例擴到正式門檻 ≥15（SPEC §19.4），涵蓋：殘缺情報引用正確率（run.py 未計的第四門檻）、
  迷霧一致性（AI 是否引用了它不該知道的敵情——WP-A1 之後這變成可測的）、MISSION 令合規率（WP-A2 之後）。
- 語料入庫優先序（依 [JCATS-A]/[JCATS-F] 的訓練語境）：準則文件 > 想定文書範本 > 裝備諸元公開資料。
  使用者供檔走 F1 管線；**不虛構語料**。

#### WP-F3 RoleManager 與 AIInvocationLog 接入活執行期　★★

盤點：活自主迴路由 `LlmFactionDecider` 直連 client，未經 RoleManager 佇列、活期無 invocation 稽核。
**規格**：worker 的 LLM 呼叫改經 RoleManager（批次佇列/OPFOR 優先級生效）；每次呼叫落
`AIInvocationLog`（prompt hash、模式、耗時、護欄結果）——這是 F5 評量與 G6 白軍確認流的資料基礎，
也是 [INDSR p.57] 回放歸因在 AI 側的對應物（「AI 當時為什麼這樣下令」可考）。
連帶收掉盤點項：`system.py` 的 `ai_loop_wired: False` 過時旗標。

#### WP-F4 MoA（Mixture-of-Agents）　★（V2.2，門檻擋在前）

SPEC_FULL §9.3 的 Proposers/Challenger/Aggregator。**開工門檻**：F1–F3 完成＋eval ≥15 例全綠＋
單模型基線的決策品質量測（WP-D1 批次：單模型 vs 規則 OPFOR 勝率）存在——沒有基線，MoA 的
增益無法證明，就不開工。

#### WP-F5 訓後評量（Training Audience Assessment）　★★★

**動機**：[JTLS-F p.1052–1053] 評估點必須**在想定規劃期預埋**（何時、蒐集什麼），不是事後撈 AAR；
[JCATS-A p.15] 評量維度＝計畫可行性/處置至當性/指揮所作業效率/操作手熟稔度，**不評勝負**；
[JCATS-F p.14] 可評分事件鏈（火力申請時效、時間管制點達成）。

**規格**：
- 想定 schema 增 `assessment_plan: [{id, objective, measure: {event_chain|response_time|compliance|manual},
  params, weight}]`——評估點是想定的一部分（可 export/import）。
- 量測型別：`response_time`（事件 A→B 的 tick 差：如遭襲→下達反應令）；`event_chain`（鏈完整性：
  call-for-fire 全鏈是否走完，WP-C10/B5 供事件）；`compliance`（違規計數：越出戰鬥地境、
  FRATRICIDE、逾時 deadline）；`manual`（白軍主觀評分項——評分 UI 給白軍，量測是人、記錄是系統）。
- 執行：評量引擎（純函數，讀 Ledger）於演後產出 per-seat 評量報告；LLM 角色 `ASSESSMENT_NARRATOR`
  可將量測結果轉寫講評草稿（AI 產敘事、不產分數——分數是確定性計算或白軍人工）。
- AAR 頁新「評量」分頁：目標×席位矩陣、時間軸標記可跳轉重播（依賴 WP-D6 地圖重播）。
- 依 [JCATS-A p.15]：評量報告預設**不顯示勝負**；勝負 DSL 在訓練型演習可設 `victory_display=WHITE_CELL_ONLY`。

**驗收**：tutorial 想定加 3 個評估點（反應時間/火協鏈/違規），跑一局後自動產出評量報告；
白軍主觀項可補評；報告與 AAR bundle 一起導出。

<a id="wp-g"></a>
### WP-G：前端工程健全化

盤點清單（README §8）逐項成卡，全部 golden 無關：

| 卡 | 內容 | 驗收 |
|----|------|------|
| G1a ✅ | **cop.vue 拆分（狀態與面板層）**：抽 composables（`useCopWidgets`/`useCopPrefs`/`useWeaponTracks`/`useUnitCardDrag`/`useCopOrdering`/`useMapEditor`）＋面板子元件（MapEditorPanel/UnitsOrderPanel/UnitDetailCard），子元件以 **`reactive(composable)` 單一 prop** 收狀態而非數十個 prop+emit | ✅ 2026-07-30。cop.vue 4419→2181；e2e 與拆分前**逐條相同**（4 failed / 14 passed，皆為既有紅燈）；多 agent 稽核確認的 5 個回歸已修。worklog: cop-decomposition.md |
| G1b ✅ | **cop.vue 拆分（版面層）**：`CopHeader`/`CopWidget`（六個小工具共用外殼）/`EquipManagerPanel`/`MapContextMenu`/`LayersPanel`/`OrdersPanel`/`EventsPanel`/`MapStateEditBar`/`CoordReadout`＋`useCtxMenu`/`useEquipMgr`/`useMapStateEdit`/`useCopFeed`/`useLiveState`/`useCopUnits` | ✅ 2026-07-30。cop.vue 4419→**951**（−78%）。**MapCanvas props 收斂經評估後不做**（50 個 prop 各有行內文件，打包會打散說明並讓通用地圖元件綁死 COP 偏好結構；理由記於 worklog）。使用者裁示「< 800 是目標不是硬指標，不用硬包」。e2e 與拆分前逐條相同（同樣四條既有紅燈），並**新增 2 支通過的 e2e**（右鍵選單、座標查詢，兩者原本零覆蓋）。12 塊逐塊等價性稽核 + 機械式孤兒/testid 掃描：0 回歸。worklog: cop-decomposition.md |
| G2 | **Tailwind 決策**：main.css 未接線（盤點）。二擇一：接上並漸進採用，或移除相依——**建議移除**（全站已是 scoped CSS 慣例，留著只是假象相依） | 無 tailwind 相依或已實際生效，二者擇一落地 |
| G3 | **E2E 補齊**：white-cell、AAR、autonomy、scenario-editor、armory、accounts、system-settings、地圖編輯/整形各至少 1 條 happy path；WP 新功能隨卡附 e2e | Playwright 綠；CI 納入 |
| G4 | **白軍控制台成熟化**：ROLLBACK 的 `window.prompt` 換正式 UI（tick 選擇器＋書籤）；單位 attributes 裸 JSON 換結構化表單；事件流換 AAR 同款格式化元件；加 WP-B2 待命注入面板 | 盤點三醜點清除 |
| G5 | **契約型別全面化**：useAar/autonomy/system-settings/scenarios 的手寫 interface 改走 `types/api.ts`（缺的端點先補 core_api.yaml——契約先行）；`useConditionDsl` 與後端 DSL 的 schema 由 contracts 單一來源生成 | `rg "interface.*View" app/` 無契約外重複定義 |
| G6 | **i18n 骨架**：字串抽 locale 檔（zh-TW 為預設），不急翻譯——先讓硬編碼停止增生 | 新增程式碼可用 t()；存量漸進 |

另收盤點雜項：demo 殘留（`?units=N`/`?demo=1`/`currentTick=100`）移到顯式 dev 旗標後；
token 改 httpOnly cookie 評估（記 ADR，涉及 SSR 架構）；WS token 從 query string 改
subprotocol/first-message（防 access log 洩漏）；contact 血量 ground truth 妥協改為 fidelity-gated
（IDENTIFIED 才顯示概略戰力）。

<a id="wp-h"></a>
### WP-H：互通與多站演習

> 定位：V2.2 探索群。[MASA-MS] 只是形態參照（3 頁行銷白皮書，無協定細節）；
> [JTLS-F] 的 HLA 工程細節才是設計輸入。**先出 ADR 再寫程式**。

#### WP-H1 多站演習架構（Master + Relay）　★★（設計先行）

[MASA-MS p.1–2] 拓撲：主站權威模擬＋遠端站 Relay 複本＋站間單一最佳化連線＋站點 Admin。
MATSO 的地基：WS envelope 事件流本質上就是「可訂閱的狀態複製流」；golden replay 證明了
「事件流重放＝狀態重建」。**設計方向（ADR-007 草案內容）**：Relay＝一個訂閱主站全事件流
（含全陣營受眾）的下游 core 實例，本地重建熱狀態、對站內 client 提供唯讀 REST/WS；
**迷霧紅線在 Relay 上同樣後端過濾**（[MASA-MS] 差距表的警告：複本外洩全知狀態）——Relay 收
加密全流但對 client 仍按 faction 過濾；下令回程統一走主站 API。斷線緩衝＝ring buffer 續傳＋
RESYNC（E3 的 /state 端點在此復用）。**V2.2 交付＝ADR＋單機雙行程 PoC**（主 core＋relay core、
delay 注入測試），不含跨網部署硬化。

#### WP-H2 DIS/HLA 互通評估　★（設計先行）

[JTLS-F] 全文＋[JCATS-F p.6–7,17]（介面標準是國軍運用首要窒礙）。V2 只做評估卡：
MATSO 實體模型 ↔ RPR-FOM 對映表、時間管理相容性分析（tick 制 vs HLA time-regulating，
[JTLS-F p.1055–1057] 的驗收準則「受訓者時間感知不被破壞」直接沿用）、
最小可行切入點（DIS PDU 單向廣播 MATSO 態勢供第三方 COP 顯示——唯讀、無所有權轉移）。產出＝ADR。

#### WP-H3 Live-Virtual 預留　★（遠期）

[JCATS-F p.3–4]（實兵位置回饋＋虛擬敵注入）。V2 僅預留：units API 已可外部改位置
（地圖狀態編輯機制）——正式化為 `POST /sessions/{id}/track-feed`（批次位置注入、來源標記 LIVE，
授權獨立 token）。不做終端整合。

#### WP-H4 災防/民事想定（遠期方向記錄）

[JTLS-F p.1058–1059]（疏散/收容/交通壅塞/白軍判效）＋[INDSR p.37–40]（MASA SYNERGY 災害場擴散）＋
台灣情境現實需求。**引擎視角**：災防 CPX 主要用移動/通聯/後勤/MSEL——交戰模組反而非必要；
V2 的 B2（MSEL 世界效果）、C7（補給體系）、H1（多站）都是它的直接前置。此處僅記錄方向，
待 B/C 群落地後另立 SPEC_CIVIL。

---

## 7. 分期路線圖與依賴序

原則：**先誠實（A）、再演習（B）、保真與分析並行（C/D）、深化收尾（E/F/G）、探索殿後（H）**。
每期內的順序即建議開工序；`→` 表硬依賴。

### V2.0「誠實的引擎」（基礎修正＋高槓桿接線）

```
[x] A1 AI 迷霧接線          ── 無依賴，第一張卡           （2026-07-29）
[x] A3 G4 no-strike 修復    ── 無依賴                     （2026-07-29）
[x] E1 活 checkpoint + RNG 序列化（D4 的前置）            （2026-07-29；ADR 007）
[x] B6 想定資產修復（fixed 旗標 roundtrip bug 先修）      （2026-07-29）
[x] E3 /state 快照端點（H1 亦復用）                       （2026-07-29）
[x] C5 comms 後果閉環（投影層）                           （2026-07-30；順帶修 STATE_DIFF 無受眾）
[x] G1a cop.vue 拆分：狀態與面板層（4419→2181）           （2026-07-30；順帶修好空轉的 typecheck 閘門）
[x] G1b cop.vue 拆分：版面層（4419→951，−78%）              （2026-07-30；MapCanvas props 收斂評估後不做）
[x] D6.1 AAR 地圖重播（量綱修正先行）                       （2026-07-30；順帶修掉 5 個既有錯）
```

> 進度標記規則：完成後於此勾選、於 §4 差距總表的「狀態」欄標日期、於 §6 該 WP 標題加 ✅ 並附
> worklog 路徑與**與規格不同的實作裁決**（例如 E1 的 Ledger 邏輯截斷）。三處要一起改，
> 否則下一個接手的 agent 會照著沒更新的那一處重做。

### V2.1「演習系統」（CPX 能力成形）

```
B5.1 ✅ 席位模型 → B5.2 ✅ 信文/申請核覆 → B5.3 ✅ 火協 gate（2026-07-30）→ B5.4 標繪分送/殲敵 REPORT（未做）
C10 ✅ 五張全數結案（2026-07-30/31）：C10.1 臨機火力申請 → C10.2 面目標射擊 → C10.3 火力計畫/排程
      → C10.4a 觀測判定 + C10.4b BDA 回報 → C10.5 陣地變換
B2 ✅ MSEL 執行引擎（2026-07-31）→ B1 ✅ 演習專案（2026-07-31）→ B4 ✅ 參數凍結簽證（2026-07-31）
A2 ✅ 任務級下令（2026-07-31，四張卡）
C1 ✅ 壓制/姿態（2026-07-31）→ C3 ✅ 乘駐車/隊形（2026-07-31）→ C2 ✅ 障礙工兵（2026-07-30）
      → C9 ✅ 誤傷語意（2026-07-30，後端；前端 affordance 未做）
C4 環境演進：C4a ✅ 晝夜（2026-07-30）→ C4b 天氣 tick 化 → C4c 煙幕
C7 後勤體系（三卡）
F3 RoleManager/稽核接線；F1 INGEST 最小切片
G3 E2E 補齊（隨各卡）＋ G4 白軍控制台
E2 認證強化；E4 監控落地
```

### V2.2「分析系統與規模」

```
D1 蒙地卡羅引擎 → D2 MOE 框架 → C6 交戰收尾+係數校準（用 D1 做校準實驗）
D3 態勢分析圖層 → A4 response cell → D7 警告引擎
D4 what-if 分支（依賴 E1）
D5 持續力分析；D6.3 匯出管線
F5 訓後評量（依賴 B5/C10 事件鏈 + D6 重播）
C8 MRM 聚合解聚（依賴 C7 帳目）
E5 負載測試/LOD；F2 語料擴充；F4 MoA（門檻制）
H1 多站 ADR+PoC；H2 DIS/HLA ADR；G5/G6 收尾
```

里程碑驗收（每期收尾跑一次完整演習劇本）：
- V2.0 exit：自主推演中 AI 因迷霧「找不到敵人」而偵察；崩潰自動復原；AAR 可視重播。
- V2.1 exit：以 `armor-breakthrough` 想定跑一場 4 席位＋白軍 MSEL 誘導＋火協審批的迷你 CPX，
  全程事件鏈可評量。
- V2.2 exit：對同一想定完成一份 [INDSR] 式分析報告（30 seeds × 3 變因、MER/KR 指標、
  一個由回放歸因得出的修模建議）。

## 8. 刻意不做（Non-Goals）

明列以免後續 agent 誤入：

1. **空戰/海戰完整域模型**——[INDSR] 的 CMO 海空案例是方法論參照，不是功能目標；MATSO 維持陸戰縱深。
   空中力量以「效果」進場：空偵申請（B5）、空襲 MSEL 注入（B2）、陸航油料 gating（遠期）。
2. **內嵌腳本語言（Lua 式）**——分析走 CSV/bundle 匯出到外部生態系（D6），不自建腳本沙箱。
3. **自研 3D 視覺**——[INDSR p.43–46] 的視覺化要求以 2D COP＋分析圖層滿足；3D 屬展示性投資，ROI 低。
4. **HLA 完整聯邦成員資格**——V2 僅 ADR 與單向 DIS 廣播評估（H2）；FEDEP 全流程是 V3+ 的事。
5. **浮動授權/商業化配額**（[MASA-MS p.3]）——記錄於此，原型階段不做。
6. **LLM 裁決任何物理**——永遠不做。所有「AI 更聰明」的路徑只允許通往：更好的令、更好的敘事、
   更好的評量草稿。

## 9. 給工程 agent 的執行守則

1. **開工前**：讀 PROGRESS.md → 讀本文件對應 WP → 讀 WP 引用的 README 章節與現行程式碼 →
   建 worklog（`docs/worklog/` 依 _TEMPLATE）→ 在 TASKS.md 登錄任務卡（流水號接續）。
2. **契約先行**：動 API/schema 的卡，第一個 commit 必須是 contracts/ 變更＋驗證通過；
   DB 變更只走 `prisma migrate`＋`schema_sync_check.py` 綠。
3. **golden 紀律**：標「golden：重錄」的卡，重錄是**最後一步**且獨立 commit；重錄前先解釋
   為什麼輸出變了（worklog）。標「預設中性」的卡要有測試證明預設值下位元不變。
4. **驗收即證據**：每條驗收標準在 worklog 附指令＋輸出摘要；容器實測用 clone session，
   驗完清理（本專案既有慣例）。
5. **一次一卡**：WP 子項若列了多卡（如 A2 四卡），逐卡走完整流程；發現範圍外問題記
   PROGRESS Backlog，不順手修。
6. **不變量檢查**（§5）自我審查後才收卡：新隨機性走了哪個 stream？新裁決是純函數嗎？
   新資訊流過迷霧投影了嗎？白軍特權 API 有雙重 RBAC 嗎？
7. **與使用者互動**：涉及軍事語意取捨（係數值、準則行為細節）時，給出建議值＋文獻依據後
   讓使用者裁示；不確定的戰術常識**不要編造**，標記「待軍事 SME 校準」。

---

*SPEC_V2 0.1｜2026-07-29｜依六份文獻與全碼庫盤點編成；後續修訂直接改本檔並在 git 訊息註明。*
