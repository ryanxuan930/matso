"""Golden replay 決定性測試（SPEC_FULL §19.1）。CI: `pytest core/tests/replay -m golden`。

`MATSO_RERECORD_GOLDEN=1` 時改為「記錄」golden 而非斷言（見 ops/tools/rerecord_golden.py）。
"""

from __future__ import annotations

import pytest
from harness import ReplayScenario, load_golden, rerecord_requested, run_replay, save_golden
from scenarios import SCENARIOS, build_rng_walk_kernel

pytestmark = pytest.mark.golden


@pytest.mark.parametrize("name", sorted(SCENARIOS))
async def test_golden(name: str) -> None:
    outcome = await run_replay(SCENARIOS[name])
    if rerecord_requested():
        save_golden(outcome)
        return
    golden = load_golden(name)
    assert outcome.final_tick == golden["n_ticks"]
    assert outcome.state_hash == golden["state_hash"], (
        f"golden replay 不符：scenario={name}\n"
        f"  期望 {golden['state_hash']}\n  實得 {outcome.state_hash}\n"
        f"若為刻意的裁決/邏輯變更，請跑 `uv run python ops/tools/rerecord_golden.py` 並在 PR 說明。"
    )


async def test_replay_is_reproducible() -> None:
    # 同想定跑兩次 → 完全相同（P4，不依賴 golden 檔）
    a = await run_replay(SCENARIOS["rng_walk_100"])
    b = await run_replay(SCENARIOS["rng_walk_100"])
    assert a.state_hash == b.state_hash


async def test_order_sequence_replay_reproducible() -> None:
    # O1.7/R10：同一「Ledger 指令序列」重播 → 相同最終 stateHash（SPEC §3.2）。
    a = await run_replay(SCENARIOS["order_replay_60"])
    b = await run_replay(SCENARIOS["order_replay_60"])
    assert a.state_hash == b.state_hash


async def test_golden_detects_logic_drift() -> None:
    # 改變一個「邏輯常數」（RNG stream_id）→ 最終狀態不同 → hash 必變。
    # 證明 harness 抓得到 drift（滿足 O1.6 驗收「改常數 → hash 比對失敗」）。
    if rerecord_requested():
        pytest.skip("rerecord 模式不驗 drift")
    baseline = load_golden("rng_walk_100")["state_hash"]
    drifted = await run_replay(
        ReplayScenario("drift", 100, lambda: build_rng_walk_kernel(stream_id="movement_v2"))
    )
    assert drifted.state_hash != baseline
