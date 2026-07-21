"""想定管理（SPEC_FULL §11）——scenario package 載入、驗證、開局。

載入器 MUST 全量驗證並回報**精確錯誤路徑**（如 `orbat/red.yaml: units[2].parent: 未知上級`）。
factions/relations 為 scenario 權威（§12.1）：建立 FactionRelations、驗證未知陣營/保留字/非法關係。
"""

from __future__ import annotations

from app.scenario.dump import dump_scenario_package, scenario_to_dict
from app.scenario.loader import (
    LoadedScenario,
    ScenarioError,
    create_session_from_scenario,
    load_scenario_package,
)

__all__ = [
    "LoadedScenario",
    "ScenarioError",
    "create_session_from_scenario",
    "dump_scenario_package",
    "load_scenario_package",
    "scenario_to_dict",
]
