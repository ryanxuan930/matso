"""交戰裁決引擎（SPEC §7.1）——純同步純函數、確定性、AI 永不介入物理。"""

from app.adjudication.aggregate import (
    AggregateEnv,
    AggregateForce,
    AggregateResult,
    resolve_aggregate_tick,
    should_aggregate,
)
from app.adjudication.engagement import (
    EngagementResult,
    EnvSnapshot,
    Resolution,
    Shooter,
    Target,
    resolve_engagement,
)
from app.adjudication.seed_weapons import SEED_WEAPONS
from app.adjudication.weapon import WeaponProfile

__all__ = [
    "SEED_WEAPONS",
    "AggregateEnv",
    "AggregateForce",
    "AggregateResult",
    "EngagementResult",
    "EnvSnapshot",
    "Resolution",
    "Shooter",
    "Target",
    "WeaponProfile",
    "resolve_aggregate_tick",
    "resolve_engagement",
    "should_aggregate",
]
