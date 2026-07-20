"""MATSO Terrain Module（M2）。DTED 高程查詢；hex grid/LOS/A* 於 O2.2–O2.4 加入。"""

from terrain.config import TerrainSettings
from terrain.dted import SEA_LEVEL_M, DtedMap, ElevationResult, WindowSample
from terrain.errors import DtedFileNotFoundError, OutOfBoundsError, TerrainError
from terrain.hexgrid import (
    CellAttributes,
    HexGridBuilder,
    HexGridCache,
    TerrainClass,
    base_mobility_cost,
    classify_terrain,
    write_parquet,
)
from terrain.los import LosResult, Observer, check_los, get_viewshed
from terrain.mobility import MobilityMatrix
from terrain.pathfind import PathResult, get_path
from terrain.plugin import TerrainPlugin, build_from_settings
from terrain.service import TerrainService

__version__ = "0.1.0"

__all__ = [
    "SEA_LEVEL_M",
    "CellAttributes",
    "DtedFileNotFoundError",
    "DtedMap",
    "ElevationResult",
    "HexGridBuilder",
    "HexGridCache",
    "LosResult",
    "MobilityMatrix",
    "Observer",
    "OutOfBoundsError",
    "PathResult",
    "TerrainClass",
    "TerrainError",
    "TerrainPlugin",
    "TerrainService",
    "TerrainSettings",
    "WindowSample",
    "__version__",
    "base_mobility_cost",
    "build_from_settings",
    "check_los",
    "classify_terrain",
    "get_path",
    "get_viewshed",
    "write_parquet",
]
