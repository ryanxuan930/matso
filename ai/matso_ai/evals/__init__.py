"""內部 WARBENCH 風格評測（SPEC_FULL §9.4 / §19.4）。

runner 入口：`python -m matso_ai.evals.run`。**案例庫可能為空**（eval 資料未到位）——空庫時
gate 降為 schema-only + EVAL_CORPUS_EMPTY 警告（§19.4 條件式 gate）。

responder 以 `--responder {fallback,replay,openai}` 注入（run.py 模組說明）：fallback 只驗管線，
replay/openai 才量模型品質；前置不成立時明確報錯，**不**靜默退回 fallback。
"""

from __future__ import annotations

from matso_ai.evals.cases import CASES_DIR, load_cases
from matso_ai.evals.citations import CORPUS_DIR, CitationIndex
from matso_ai.evals.run import EvalReport, FallbackResponder, run_evals

__all__ = [
    "CASES_DIR",
    "CORPUS_DIR",
    "CitationIndex",
    "EvalReport",
    "FallbackResponder",
    "load_cases",
    "run_evals",
]
