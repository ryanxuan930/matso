---
task: "#100 README.md 全系統文件 + SPEC_V2.md 差距分析與 v2 開發規格"
status: DONE
started: 2026-07-29T01:05+08:00
updated: 2026-07-29T02:05+08:00
agent: Fable 5（architect pass；後續開發交 Opus 5）
spec: 使用者指示 + 6 份有效參考文獻（詳下）
---

# #100 README.md + SPEC_V2.md

## 目標摘要
1. `README.md`（root，1556 行）：整個系統的架構、功能、細部設計——每個檔案的用途與相互關係。
2. `SPEC_V2.md`（900 行）：對照參考文獻的差距分析（35 項）＋ 8 個工作包群（A–H）的
   可開工級規格，含分期路線圖（V2.0/2.1/2.2）與工程 agent 執行守則。

## 方法（可重現）
- Workflow `wf_d1d30c8c-f9c`：13 agents 並行——6 個碼庫 mapper（engine/domain/api/frontend/infra/docs+ai，
  逐檔盤點＋各自的「現況限制與缺口」節）+ 7 個 PDF reader（每份讀完整本）。
  首輪 9 個撞用量上限，resume 快取續跑全數完成（1.37M subagent tokens）。
- 產物存 scratchpad `research/`（map_*.md ×6、pdf_*.md ×6）；journal 在
  `~/.claude/projects/.../subagents/workflows/wf_d1d30c8c-f9c/journal.jsonl`。
- README＝我寫總覽/資料流/紅線/閘門章節＋六份 mapper 輸出降級標題後機械組裝（§5–§10）。
- SPEC_V2＝我依 PDF 差距候選＋mapper 缺口節統整撰寫（全文手寫，非拼裝）。

## 文獻識別結論（重要）
使用者 7 份 PDF 實為 **6 個有效來源**：
- `f3115813…pdf`＝INDSR 特刊《模式模擬與電腦兵棋推演》**97 頁全本**（非標稱 170 頁）；
  `7841b529…pdf`（標稱 76 頁實為 14 頁）與 `130.pdf` 皆為**其 pp.34–49 節錄**，內容全被全本涵蓋。
- 其餘：NATO MP-IST-160（MASA 決策支援）、陸院 JCATS 裝甲旅論文、砲兵季刊 JCATS 簡介、
  MITRE JTLS-JCATS Federation、MASA Multi-Site CPX 白皮書。

## 盤點中的重大發現（已寫入兩份文件）
- **AI 敵情仍用 ground truth**（IntelService 已上線、orchestrator 未接）→ SPEC_V2 第一張卡 WP-A1。
- **G4 no-strike 形同空轉**（只認 target_h3、AI 令帶 lat/lng；no_strike_hexes 恆空）→ WP-A3。
- 活執行期 TriggerChecker 仍 NoOp（MSEL 無世界效果）；活 session 未掛 checkpointer；
  `resolve_multiway_tick` 已實作未接線；scenario dump 丟 `fixed` 旗標（roundtrip bug）；
  前端 Tailwind 未接線、RESYNC 半套。
- 記憶檔 `live-runtime-subsystems.md` 過時（sensors/comms/logistics 皆已接線）——已更新。

## 檔案異動
| 檔案 | 動作 | 說明 |
|------|------|------|
| README.md | 新增 | 14 章：總覽 4 章（手寫）+ 子系統解剖 6 章（mapper 組裝）+ 資料流/紅線/閘門/缺口 4 章 |
| SPEC_V2.md | 新增 | §1–9：文獻基礎、35 項差距總表、7 條設計不變量、WP-A~H 詳規、三期路線圖、Non-Goals、執行守則 |

## 中斷續作指引
- 本卡完成。下一步＝使用者裁示 V2.0 起手卡（建議 WP-A1，SPEC_V2 §7）。
- 文獻精讀全文若需回查：scratchpad research/ 目錄（session 存續期間有效）；重要結論已內化進 SPEC_V2 §2。
