"""MATSO Comms/EW Module（M5）——鏈路預算 + networkx mesh 連通（SPEC §6）。"""

from comms.link_budget import (
    DEGRADED_MARGIN_DB,
    ONLINE_MARGIN_DB,
    LinkState,
    Radio,
    free_space_path_loss_db,
    haversine_m,
    link_margin_db,
    link_state_from_margin,
)
from comms.mesh import (
    CommsUnitInput,
    LinkResult,
    UnitResult,
    resolve_comms,
)
from comms.plugin import CommsPlugin
from comms.service import CommsService

__version__ = "0.1.0"

__all__ = [
    "DEGRADED_MARGIN_DB",
    "ONLINE_MARGIN_DB",
    "CommsPlugin",
    "CommsService",
    "CommsUnitInput",
    "LinkResult",
    "LinkState",
    "Radio",
    "UnitResult",
    "__version__",
    "free_space_path_loss_db",
    "haversine_m",
    "link_margin_db",
    "link_state_from_margin",
    "resolve_comms",
]
