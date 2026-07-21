# ai/rag/corpus/ — RAG 語料庫（入庫來源）

O6.3 的入庫 CLI 從這裡讀取 → 語意切塊（512 tokens, overlap 64）→ bge-m3 嵌入 →
寫入 Qdrant collection。**本目錄是「資料」，不是程式**——但每份文件都會被引用查核
（護欄 G5）核對，所以內容必須「可被逐字引用」。

## Collections（SPEC_FULL §9.4，共 6 個，勿增減名稱）

| 目錄 | 內容 | 主要使用角色 |
|------|------|--------------|
| `doctrine_general/` | **通用軍事準則（不確定歸哪就放這）**——公開文獻（FM、智庫、DTIC）多屬此類 | 全角色 |
| `doctrine_blue/` | 真正藍軍特化的準則、SOP、ROE 樣板（若有） | STRATEGIC_PLANNER |
| `doctrine_red/` | 真正紅軍特化的準則、作戰慣性（若有） | OPFOR_COMMANDER |
| `equipment_specs/` | 裝備參數（射程、感測、速度、耗油…） | 全角色（引用查核） |
| `terrain_analysis/` | 地形分析原則、通道/隘口/掩蔽準則 | STRATEGIC_PLANNER / OPFOR |
| `historical_ops/` | 歷史戰例（敘事 + 教訓） | AAR_ANALYST |

**空語料是常態，不是錯誤**（SPEC_FULL §9.0/§9.4）：任一 collection 可為空；AI 於空庫時自動
降級 `AI_BARE`（依模型自身判斷、引用必為空），或整個 AI 以 `AI_OFF` 關閉走傳統兵推。
語料是增強，不是系統運作的前提——**不必為了「填滿」而降低內容品質**。

**PDF/掃描/圖檔不直接進本目錄**：先走文檔轉換管線（**SPEC_INGEST.md**，O9）→ staging
→ 人工審核 promote → 才落地此處。手工只寫原生 markdown 短文。

## 硬性規則

1. **只放 UNCLASSIFIED／合成內容**。真機密不得進向量庫（air-gap 是防外流，不是放行機密）。
2. **一節一概念**，段落勿過長——方便語意切塊落在乾淨邊界（目標 ~512 token/節）。
3. **可逐字引用**：放具體、可查核的斷言（數字、TTP、門檻）。空泛口號無法當 RAFT golden，
   也過不了 G5 相似度閾值。
4. **穩定標題錨點**：每個可引用段落給一個穩定 id（如 `## [RED-DELAY-03] …`）。
   引用格式為 `doctrine_red/red_delay_ops.md#RED-DELAY-03`（錨點比行號穩，不隨編輯漂移）。

## 每份文件的 front-matter（YAML）

```markdown
---
collection: doctrine_red        # 必須是上表 6 個之一
source: "自編合成準則 v1"        # 來源（合成請註明）
classification: UNCLASSIFIED     # 只允許 UNCLASSIFIED
version: "2026-07"
doctrine_side: RED              # RED / BLUE / NA
---

## [RED-DELAY-01] 段落標題（穩定錨點）
具體、可引用的內容…
```

範例見 [`doctrine_red/red_delay_ops.md`](doctrine_red/red_delay_ops.md)。

## 起步份量（夠跑通 O6.3 ingest→retrieve→citation-check roundtrip）

**沒有最低門檻**——空庫也是合法狀態（AI 自動降級）。若要跑通完整 `AI_FULL` roundtrip，
最小集：doctrine_general 2–3 篇即可。紅/藍特化語料視軍方資料到位再補，不阻塞任何開發。
