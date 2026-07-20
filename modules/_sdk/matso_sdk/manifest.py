"""插件 Manifest 與種類（SPEC §16.3 / plugin_base.proto Manifest）。"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class PluginKind(enum.StrEnum):
    TERRAIN = "TERRAIN"
    WEATHER = "WEATHER"
    VISION = "VISION"
    AI_ROLE = "AI_ROLE"
    CUSTOM = "CUSTOM"


@dataclass(frozen=True, slots=True)
class Manifest:
    """插件自報身分。contract_version 為 semver；Orchestrator 比對 major 決定是否載入。"""

    name: str  # 全域唯一，如 "terrain"
    kind: PluginKind
    contract_version: str  # semver，如 "0.1.0"
    capabilities: tuple[str, ...] = field(default_factory=tuple)

    @property
    def major(self) -> int:
        """semver 主版本——Orchestrator 相容性判斷用（major 不合 → 拒絕載入）。"""
        return int(self.contract_version.split(".", 1)[0])
