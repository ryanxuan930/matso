---
task: WP-A2          # SPEC_V2 §6 WP-A2（任務級下令與準則分解器）
status: IN_PROGRESS
started: 2026-07-31T19:00+08:00
updated: 2026-07-31T19:00+08:00
agent: Opus 5
---

# WP-A2 任務級下令與準則分解器

## 目標摘要

[IST160 p.4–5] 的核心論證：成熟系統下的是**任務**（Attack(axis, objective, limit lines)），
由準則庫展開成路徑/梯隊/交戰/脫離；一人可指揮整旅。

MATSO 現在**人與 LLM 都在微操三種低階令**——LLM 每個心跳要重新推理「下一步走哪」，
呼叫頻率高、幻覺面積大。**把分解交給符號層正是 Neuro-Symbolic 的本義**
（SPEC_V2 §3 的第 3 條原則：任務級指揮的分解器是符號層，LLM 只選任務與參數）。

## 切卡（規格自己建議 4 張）

1. 契約 + `decomposer.py` 純函數 + 分解快照測試。
2. `mission_runtime.py` 接進 Kernel + 事件 + golden。
3. LLM 詞彙表 + G3 擴充 + 自主推演實測。
4. COP 下令 UI + AAR 任務時間軸。

## 執行紀錄
