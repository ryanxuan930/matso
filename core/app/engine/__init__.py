"""模擬核心引擎（SPEC_FULL §3）。

決定性基礎設施（SimClock / DeterministicRNG）是 P4 可重現性的地基，
Kernel、裁決、偵測、通訊等所有子系統的時間與隨機性皆源自此處。
"""

from app.engine.clock import SimClock, SimTime
from app.engine.rng import DeterministicRNG

__all__ = ["DeterministicRNG", "SimClock", "SimTime"]
