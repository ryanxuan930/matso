"""偵測與情報（SPEC §7.2 / §13.3）——per-faction intel store，fog of war 後端強制。"""

from app.intel.schemas import ContactView
from app.intel.seed_sensors import SEED_SENSORS
from app.intel.sensor import (
    DetectionEnv,
    SensorProfile,
    detect_probability,
    fidelity_for,
)
from app.intel.service import IntelService
from app.intel.sweep import Contact, SensorUnit, TargetUnit, sweep

__all__ = [
    "SEED_SENSORS",
    "Contact",
    "ContactView",
    "DetectionEnv",
    "IntelService",
    "SensorProfile",
    "SensorUnit",
    "TargetUnit",
    "detect_probability",
    "fidelity_for",
    "sweep",
]
