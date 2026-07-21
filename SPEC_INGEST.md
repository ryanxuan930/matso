# SPEC_INGEST — 文檔轉換子系統（Document → RAG Corpus Pipeline）

> 版本 0.1（2026-07-21，草案）。獨立於 SPEC_FULL 的子系統規格；SPEC_FULL §9.4 引用本檔。
> 任務卡見 TASKS.md **O9** 群組。狀態：**設計已定，實作未開始**。

## 1. 動機

實際可得的 RAG 來源是 **PDF 與掃描圖檔**（美軍 FM 系列、CNAS 智庫報告、DTIC 技術報告
ADA596797/ADA225466 等），不是手寫 markdown。人工轉錄不可規模化；但全自動入庫又危險——
OCR 錯字、表格錯位、圖說錯配會直接變成 AI 的「引用事實」（G5 只驗存在性，不驗真實性）。
因此需要一個**半自動**管線：機器轉換 + 人工審核閘門，產出符合 `ai/rag/corpus/README.md`
格式的 markdown，才交給 O6.3 入庫 CLI。

## 2. 設計原則

1. **air-gapped**：全部工具本機執行（PyMuPDF、本機 OCR），不呼叫雲端 API。
2. **人工審核是硬閘門**：機器產出一律落在 `staging/`，人工核可後才進 `corpus/`。
   未經審核的內容 MUST NOT 被入庫 CLI 接受（目錄隔離即機制）。
3. **可追溯**：每份產出 front-matter 記錄來源檔 hash、轉換工具版本、審核者欄位。
4. **來源合規**：只處理公開/UNCLASSIFIED 文件；管線不做密級判斷——放進 `inbox/` 的
   文件由人負責保證可用（同 corpus 紅線）。
5. **獨立子系統**：`ai/ingest/`（Python，入 uv workspace）；不依賴 core，可單獨執行。

## 3. 管線（`ai/ingest/`）

```
inbox/*.pdf|png|jpg
  → [P1] 解析：PyMuPDF 抽文字層（born-digital PDF）
  → [P2] OCR fallback：無文字層/掃描頁 → 本機 OCR（tesseract；中文另配 PaddleOCR，皆本機）
  → [P3] 結構化：頁面 → 章節偵測（heading 樣式/編號）→ 語意分節（~512 token/節）
         → 自動生成錨點 id（<檔名縮寫>-<序號>）→ front-matter 骨架（collection 待人工定）
  → staging/<來源檔名>/<節>.md + conversion_report.md（低信心頁清單、表格/圖形告警）
  → [人工審核] 修正 OCR 錯字、指定 collection（多半是 doctrine_general）、確認錨點
  → ai/rag/corpus/<collection>/ （之後走 O6.3 入庫）
```

- **表格**：轉 markdown table，低信心（跨頁/合併儲存格）標 `<!-- INGEST-REVIEW: table -->`。
- **圖/圖表**：不 OCR 內容，插入佔位註記（圖號 + caption 文字），供審核者決定是否人工描述。
- **信心分級**：每節標 `confidence: high|medium|low`（文字層=high、OCR 依引擎信心值）；
  審核報告按信心排序，人工時間花在刀口上。

## 4. CLI

```
uv run python -m matso_ai.ingest.cli convert <inbox 檔案|目錄> [--out staging/]
uv run python -m matso_ai.ingest.cli report  # 列出 staging 待審核項與低信心節
uv run python -m matso_ai.ingest.cli promote <staging 項> --collection doctrine_general \
    --reviewer <name>   # 校驗格式（front-matter/錨點唯一性）後移入 corpus/
```

`promote` 是唯一寫入 `corpus/` 的路徑，強制填 reviewer——審核紀錄進 front-matter。

## 5. 驗收（對應 O9 卡）

- **O9.1**（P1 文字 PDF）：born-digital PDF → staging markdown；分節 ~512 token、錨點唯一、
  front-matter 完整；`promote` 校驗 + reviewer 強制；roundtrip 測試（合成 PDF fixture）。
- **O9.2**（P2 OCR）：掃描頁/圖檔 → OCR → 同格式產出 + 信心分級；低信心節進報告；
  中英混排 fixture 測試。
- **O9.3**（P3 表格/報告強化）：表格轉換 + 告警註記；`report` 彙總；與 O6.3 入庫 CLI 串接
  的端到端測試（inbox → staging → promote → ingest → 檢索命中）。

## 6. 非目標（v0.1）

- 不做自動密級判斷、不做自動紅/藍分類（人工在 promote 時指定，預設 doctrine_general）。
- 不處理影音；不做版面完美重建（語意檢索用途，非排版復原）。
- 不自動入庫（promote 後仍由 O6.3 CLI 顯式執行——兩段式，各自可稽核）。

## 7. 依賴與風險

- PyMuPDF（AGPL——**內部工具使用可，不隨產品散佈**；如有疑慮改 pdfplumber/pypdf 組合）。
- tesseract 中文精度有限 → PaddleOCR 本機模型為中文主力；模型檔納入外接資產管理
  （同 TW_ALL.tif 慣例，env 注入路徑 + 缺失時優雅降級為「僅文字層模式」）。
- OCR 幻覺風險由「人工審核 + 信心分級」承擔——**管線不宣稱全自動**。
