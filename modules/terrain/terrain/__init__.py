"""MATSO Terrain Module（M2）。DTED 高程查詢；hex grid/LOS/A* 於 O2.2–O2.4 加入。"""

from terrain.config import TerrainSettings
from terrain.dted import SEA_LEVEL_M, DtedMap, ElevationResult
from terrain.errors import DtedFileNotFoundError, OutOfBoundsError, TerrainError

__version__ = "0.1.0"

__all__ = [
    "SEA_LEVEL_M",
    "DtedFileNotFoundError",
    "DtedMap",
    "ElevationResult",
    "OutOfBoundsError",
    "TerrainError",
    "TerrainSettings",
    "__version__",
]
