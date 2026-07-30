---
task: V2.1 WP-F1
status: DONE
started: 2026-07-30T00:00+08:00
updated: 2026-07-30T00:00+08:00
agent: Opus 5
---

# WP-F1 SPEC_INGEST 最小切片

## 目標摘要

`load_bge_m3()` 落地、ingest CLI 端到端（含 `corpus_manifest`）、檢索 hit-rate 評測
——「**語料品質從第一天就被量測**」。

## 核心裁決：取不到模型是「回 None」不是「拋」

模型不在（沒裝套件、沒有模型檔、載入失敗）是**部署現實**，不是程式錯誤：
本專案的 RAG 語料長期不足是設計前提，`AI_BARE` 模式本來就要能跑。
`load_bge_m3()` 回 `None` 讓呼叫端**明確地**決定降級——拋例外會讓 ingest CLI
在沒有模型的機器上根本跑不起來。

**但降級一定要看得見。** 三個地方同時標示：
1. `describe_embedder()` 給 UI 用的 `degraded` 旗標與說明。
2. CLI stdout 明說「⚠ 檢索品質降級」。
3. `corpus_manifest.json` 記下**當時用的是哪一種嵌入器**——事後看到一批品質差的檢索結果，
   沒有這一欄就無從判斷是語料不好還是嵌入器降級了。

無聲降級會讓人以為檢索品質正常而**信任它的引用**，那比檢索不到更危險。

## 為什麼是 hit@k 而不是更精緻的指標

hit@k 只問「期望的那份文件有沒有進前 k 名」。不需要相關性分級、標註者一致性、大批標註
——而本專案的資料現實是語料與 eval 長期不足。**一個能在 5 條 QA 對上就給出訊號的指標，
比一個要 500 條才有意義的指標有用得多。**

⚠ **`total=0` 不算通過**。空語料回 `hit_rate=0.0` 讓 CI 看得到「還沒有語料」這個事實，
但 `passed` 是 False——「沒有東西可測」與「測了而且過了」是完全不同的兩件事。

## 檔案異動

| 檔案 | 動作 | 說明 |
|------|------|------|
| ai/matso_ai/rag/embedder.py | 修改 | `Bge3Embedder` + `load_bge_m3()`（惰性、非硬相依）+ `describe_embedder()` |
| ai/matso_ai/rag/ingest.py | 修改 | `--embedder bge-m3`（取不到即降級）+ `write_manifest()` |
| ai/matso_ai/evals/retrieval.py | 新增 | hit@k 評測；空語料不算通過；壞 query 算未命中 |
| ai/tests/test_rag_ingest_f1.py | 新增 | 12 條 |

## 測試證據

- `uv run pytest -q -m "not benchmark"` → **1909 passed, 8 skipped, 4 deselected**
- ruff / mypy(264) → clean
- 突變測試 5 個全數被抓：空語料算通過、降級不標示、取不到模型改用拋、
  hit@k 不看 k、manifest 不記嵌入器

## 決策與陷阱

**`sentence-transformers` 不是硬相依。** air-gapped 機器上才會裝，所以 import 在函式內、
mypy 用 `type: ignore[import-not-found]` 並註明那是**預期**的。

**`local_files_only=True`**：不傳路徑而讓套件自己去下載，在斷網機器上會變成一次數十秒的
逾時而不是一個明確的失敗。

**hit-rate 門檻 0.5 是刻意的低。** 語料從零開始長，一開始就設 0.8 只會逼人關掉這個關卡。

**⚠ 又一條測試被突變測試修正（本 session 第六次）。** 「排名在 k 之外不算命中」那條的
fake retriever **自己就先 `[:k]` 了**，所以拿掉 `evaluate_retrieval` 的切片測試照樣綠。
要驗到那道防護，fake 必須**真的**回超過 k 筆（`_IgnoresK`）。

## 中斷續作指引

- **下一步第一件事**：G3 E2E 補齊 + G4 白軍控制台。
- **未竟項**（都需要使用者的部署環境決定，見 PROGRESS Backlog）：
  1. **`sentence-transformers` / 模型檔未納入部署資產**——`load_bge_m3()` 已就緒，
     但要真的跑起來需要 (a) 把套件加進 air-gapped 的安裝清單、(b) 把 bge-m3 模型檔放上機器
     並設 `MATSO_BGE_M3_PATH`。**這兩件事是部署決定，不是程式決定。**
  2. **PDF/DOCX → parse 未接進 CLI**：`ingest.py` 目前只吃 markdown（既有 `parse.py`
     的接線屬 O9 群）。OCR fallback 同理。
  3. **retrieval QA 對還沒有任何一批**——評測跑得起來但 `total=0`（而且正確地不算通過）。
  4. G5 引用查核與 `citation_verifier` 注入仍待語料非空。
