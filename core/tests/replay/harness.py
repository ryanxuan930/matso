"""Golden replay harness（SPEC_FULL §19.1）。

golden replay = 相同 (master_seed, 想定) 從 tick 0 重跑 → 最終 stateHash 必須與 golden 一致，
證明 P4 可重現性。replay 全在記憶體進行（InMemoryHotState + NullMonotonicClock + null sink），
無 DB / Redis / 牆鐘依賴，因此完全確定性。

goldens 存於 goldens/<name>.json。裁決邏輯刻意變更時，以
`MATSO_RERECORD_GOLDEN=1` 執行 golden 測試（或 ops/tools/rerecord_golden.py）重新記錄，
並在 PR 說明變更（§19.1）。
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.engine.kernel import Kernel
from app.state.checkpoint import compute_state_hash

GOLDENS_DIR = Path(__file__).parent / "goldens"
RERECORD_ENV = "MATSO_RERECORD_GOLDEN"


@dataclass(frozen=True, slots=True)
class ReplayScenario:
    """一個可重現想定：名稱、tick 數、以及建立「全確定性 Kernel」的工廠。

    build_kernel 每次呼叫 MUST 回傳全新的 Kernel（新 hot_state、新 RNG），以確保重跑獨立。
    """

    name: str
    n_ticks: int
    build_kernel: Callable[[], Kernel]


@dataclass(frozen=True, slots=True)
class ReplayOutcome:
    name: str
    final_tick: int
    state_hash: str


async def run_replay(scenario: ReplayScenario) -> ReplayOutcome:
    kernel = scenario.build_kernel()
    await kernel.run(scenario.n_ticks)
    state = kernel.hot_state.get_all()
    return ReplayOutcome(
        name=scenario.name,
        final_tick=scenario.n_ticks,
        state_hash=compute_state_hash(state),
    )


def rerecord_requested() -> bool:
    return bool(os.environ.get(RERECORD_ENV))


def _golden_path(name: str) -> Path:
    return GOLDENS_DIR / f"{name}.json"


def load_golden(name: str) -> dict[str, Any]:
    data: dict[str, Any] = json.loads(_golden_path(name).read_text(encoding="utf-8"))
    return data


def save_golden(outcome: ReplayOutcome) -> None:
    GOLDENS_DIR.mkdir(parents=True, exist_ok=True)
    payload = {
        "name": outcome.name,
        "n_ticks": outcome.final_tick,
        "state_hash": outcome.state_hash,
    }
    text = json.dumps(payload, indent=2, ensure_ascii=False) + "\n"
    _golden_path(outcome.name).write_text(text, encoding="utf-8")
