"""DeterministicRNG — 受控的決定性隨機數來源（SPEC_FULL §3.2、HOW_TO §4.1）。

紅線：模擬邏輯 MUST NOT 使用裸 `random` 模組或任何未受控的隨機性。一切抽樣經由本類別，
種子由 Session 的 master_seed 派生，確保 golden replay 產生 bit-identical 結果（P4）。

每個子系統（"adjudication" / "sensors" / "comms" …）使用獨立 stream_id，各自的產生器
完全不共用狀態——如此在某子系統增減抽樣次數，不會擾動其他子系統的隨機序列，
避免跨系統耦合破壞可重現性。
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, TypeVar

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Sequence

T = TypeVar("T")


def _derive_seed(master_seed: int, stream_id: str) -> int:
    """以 SHA-256 將 (master_seed, stream_id) 折疊成 256-bit 子種子。

    採固定 big-endian 位元組序，確保跨平台（含大小端機器）產生相同種子。
    numpy SeedSequence 接受任意大小的非負整數，內部轉為 uint32 陣列。
    """
    digest = hashlib.sha256(f"{master_seed}:{stream_id}".encode()).digest()
    return int.from_bytes(digest, "big")


class DeterministicRNG:
    """單一 stream 的決定性產生器，底層為 numpy PCG64。

    numpy 保證其 BitGenerator 的位元串在不同版本與平台間穩定，故適合可重現模擬。
    以 (master_seed, stream_id) 建構後，序列完全確定。
    """

    def __init__(self, master_seed: int, stream_id: str) -> None:
        if not stream_id:
            raise ValueError("stream_id 不可為空——每個 stream 必須有明確身分")
        self._master_seed = master_seed
        self._stream_id = stream_id
        self._gen = np.random.Generator(np.random.PCG64(_derive_seed(master_seed, stream_id)))

    @property
    def stream_id(self) -> str:
        return self._stream_id

    def random(self) -> float:
        """回傳 [0.0, 1.0) 的均勻亂數。"""
        return float(self._gen.random())

    def uniform(self, low: float, high: float) -> float:
        """回傳 [low, high) 的均勻亂數。"""
        return float(self._gen.uniform(low, high))

    def choice(self, seq: Sequence[T]) -> T:
        """從非空序列中均勻取一個元素。

        以產生索引的方式實作（而非 numpy 的 Generator.choice），
        避免 numpy 將任意 Python 物件序列強制轉為 ndarray 而改變型別或行為。
        """
        if len(seq) == 0:
            raise ValueError("無法從空序列 choice")
        index = int(self._gen.integers(len(seq)))
        return seq[index]
