---
task: O0.0            # TASKS.md 的任務編號
status: IN_PROGRESS   # IN_PROGRESS / BLOCKED / DONE
started: 2026-01-01T00:00+08:00
updated: 2026-01-01T00:00+08:00   # 每次編輯本檔都要更新
agent: Opus 4.8       # 或其他模型名
---

# O0.0 <任務標題>

## 目標摘要
（用自己的話重述任務目標 2–3 行，證明有讀懂 TASKS.md 與規格。）

## 計畫（開工時先寫，邊做邊勾）
- [ ] 步驟 1
- [ ] 步驟 2

## 執行紀錄（附時間，由上而下追加，不要改寫舊條目）
- `14:02` 讀完 SPEC_FULL §3.2，決定 RNG 用 numpy PCG64 而非 random.Random，因為…
- `14:15` 完成 rng.py 初版；發現 stream 折疊需處理…；測試 X 通過。

## 檔案異動（給人與後續 agent 看的「這段程式在做什麼」）
| 檔案 | 動作 | 說明 |
|------|------|------|
| core/app/engine/rng.py | 新增 | DeterministicRNG：… 關鍵設計：… |

## 測試證據（指令 + 結果摘要，失敗也要記）
- `uv run pytest core/tests/unit -q` → 12 passed
- `uv run mypy` → clean

## 決策與陷阱（之後維護的人需要知道的 why）
- 為什麼這樣做而不是那樣做；踩過什麼坑。

## 中斷續作指引（⚠ 每次停筆前必須是最新狀態）
- **下一步第一件事**：…
- **目前卡點 / 未解問題**：…
- **尚未驗證的假設**：…
