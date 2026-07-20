"""種子感測器模板（O3.3）——EquipmentTemplate.baseStats 格式（sensor $def）。v0 佔位值。

detect_curve 依 range 遞增、p_detect 非遞增（近距易偵測）。對 weaponeering.schema.json 驗證。
"""

from __future__ import annotations

from typing import Any

SEED_SENSORS: dict[str, dict[str, Any]] = {
    "EO_DAY": {  # 日間光學
        "sensor_kind": "OPTICAL",
        "max_range_m": 4000,
        "detect_curve": [[500, 0.95], [2000, 0.75], [4000, 0.40]],
    },
    "IR_THERMAL": {  # 熱像
        "sensor_kind": "IR",
        "max_range_m": 3000,
        "detect_curve": [[500, 0.90], [1500, 0.70], [3000, 0.45]],
    },
    "GROUND_RADAR": {  # 對地雷達
        "sensor_kind": "RADAR",
        "max_range_m": 8000,
        "detect_curve": [[1000, 0.85], [4000, 0.65], [8000, 0.35]],
    },
    "ACOUSTIC_ARRAY": {  # 聲學陣列（不需 LOS）
        "sensor_kind": "ACOUSTIC",
        "max_range_m": 1500,
        "detect_curve": [[300, 0.70], [800, 0.45], [1500, 0.20]],
    },
}
