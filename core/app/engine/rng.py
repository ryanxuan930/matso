"""DeterministicRNG — 受控的決定性隨機數來源（SPEC_FULL §3.2、HOW_TO §4.1）。

紅線：模擬邏輯 MUST NOT 使用裸 `random` 模組或任何未受控的隨機性。一切抽樣經由本類別，
種子由 Session 的 master_seed 派生，確保 golden replay 產生 bit-identical 結果（P4）。

每個子系統（"adjudication" / "sensors" / "comms" …）使用獨立 stream_id，各自的產生器
完全不共用狀態——如此在某子系統增減抽樣次數，不會擾動其他子系統的隨機序列，
避免跨系統耦合破壞可重現性。
"""

from __future__ import annotations

import copy
import hashlib
from typing import TYPE_CHECKING, Any, TypeVar

import numpy as np

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

T = TypeVar("T")

_BIT_GENERATOR = "PCG64"


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

    def get_state(self) -> dict[str, Any]:
        """回傳可序列化的產生器狀態快照（WP-E1；checkpoint 用）。

        `numpy` 區段即 `bit_generator.state`——全為 Python `str`/`int`（含兩個 128-bit 整數），
        經 canonical JSON 往返無損（有測試釘住）。`has_uint32`/`uinteger` 是 numpy 的半顆
        32-bit 快取，**必須一併保存**：漏了它，復原後第一次 `choice()`（走 32-bit 路徑）
        就會與崩潰前分歧。

        回傳的是深拷貝——不可讓呼叫端拿到內部參照，否則後續抽樣會就地改寫「快照」。
        `stream_id` 一併帶出供 `set_state` 驗身分（見該方法）。
        """
        return {
            "stream_id": self._stream_id,
            "numpy": copy.deepcopy(self._gen.bit_generator.state),
        }

    def set_state(self, state: Mapping[str, Any]) -> None:
        """由 `get_state()` 的快照還原產生器（WP-E1；崩潰復原用）。

        **只還原位置、不重建產生器**——`master_seed`/`stream_id` 不變。

        兩道驗身分：stream 不符或 bit generator 型別不符一律拒絕。把 movement 的狀態
        灌進 adjudication 的產生器不會報錯（numpy 照收），但整局的隨機序列就此偏離
        「同種子同結果」的保證，而且**沒有任何症狀**——這種靜默失效必須在入口擋掉。
        """
        stream = state.get("stream_id")
        if stream is not None and stream != self._stream_id:
            raise ValueError(
                f"RNG 狀態 stream 不符：快照為 {stream!r}，本產生器為 {self._stream_id!r}"
            )
        numpy_state = state.get("numpy")
        if not isinstance(numpy_state, dict):
            raise ValueError("RNG 狀態缺少 numpy 區段")
        if numpy_state.get("bit_generator") != _BIT_GENERATOR:
            raise ValueError(
                f"RNG 狀態的 bit generator 為 {numpy_state.get('bit_generator')!r}，"
                f"本實作為 {_BIT_GENERATOR}"
            )
        self._gen.bit_generator.state = copy.deepcopy(numpy_state)

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
