"""機動成本模型（contracts/mobility_matrix.json 的載入與求值；O2.4 A* 用）。

**契約先行（紅線 4）**：成本公式的唯一權威是 `contracts/mobility_matrix.json`：

    step_cost = profiles[profile][terrain_class] × (1 + slope_penalty[profile] × slope_deg / 45)

其中 `profiles[profile][terrain_class] == -1` 代表**不可通行**（回 None）。

注意：本模組**不**乘 `CellAttributes.mobility_cost`——那個 profile 無關欄位本身已把坡度
以 `1 + slope/15` 併入，若再結合此處的 slope_penalty 會重複計算坡度。A* 一律走此契約公式。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

_MODULE_ROOT = Path(__file__).resolve().parents[1]
# 預設契約位置（repo 內；小型可覆寫資料，非外接硬碟大資產）。想定可經 overrides/ 覆寫。
_DEFAULT_MATRIX_PATH = _MODULE_ROOT.parents[1] / "contracts" / "mobility_matrix.json"

_SLOPE_REF_DEG = 45.0  # slope_penalty 公式的參考坡度（契約 $comment）
IMPASSABLE = -1.0


@dataclass(frozen=True, slots=True)
class MobilityMatrix:
    """profile × terrain_class 通行成本表 + 坡度懲罰係數。"""

    profiles: dict[str, dict[str, float]]
    slope_penalty: dict[str, float]
    version: str = "unknown"

    @classmethod
    def from_dict(cls, data: dict[str, object]) -> MobilityMatrix:
        profiles_raw = data.get("profiles")
        slope_raw = data.get("slope_penalty")
        if not isinstance(profiles_raw, dict):
            raise ValueError("mobility_matrix 缺少 'profiles' 物件")
        if not isinstance(slope_raw, dict):
            raise ValueError("mobility_matrix 缺少 'slope_penalty' 物件")
        profiles = {
            str(profile): {str(cls_): float(cost) for cls_, cost in row.items()}
            for profile, row in profiles_raw.items()
        }
        # slope_penalty 可能含 $comment 說明鍵——只取值為數字者
        slope_penalty = {
            str(profile): float(v) for profile, v in slope_raw.items() if isinstance(v, int | float)
        }
        version = str(data.get("version", "unknown"))
        return cls(profiles=profiles, slope_penalty=slope_penalty, version=version)

    @classmethod
    def load(cls, path: Path) -> MobilityMatrix:
        with path.open(encoding="utf-8") as fh:
            return cls.from_dict(json.load(fh))

    @classmethod
    def default(cls) -> MobilityMatrix:
        """載入 repo 內建 contracts/mobility_matrix.json。找不到即明確報錯（不靜默降級）。"""
        if not _DEFAULT_MATRIX_PATH.is_file():
            raise FileNotFoundError(
                f"找不到內建 mobility_matrix.json：{_DEFAULT_MATRIX_PATH}；"
                "請以 MobilityMatrix.load(path) 明確指定或傳入矩陣。"
            )
        return cls.load(_DEFAULT_MATRIX_PATH)

    def has_profile(self, profile: str) -> bool:
        return profile in self.profiles

    def step_cost(self, profile: str, terrain_class: str, slope_deg: float) -> float | None:
        """進入該 (terrain_class, slope) cell 的成本；不可通行回 None。

        契約公式：base × (1 + slope_factor × slope/45)。slope 夾在 [0, 45] 避免負/爆量。
        """
        row = self.profiles[profile]  # 呼叫方先以 has_profile 驗證
        base = row.get(terrain_class)
        if base is None:
            raise KeyError(f"mobility_matrix[{profile}] 未定義 terrain_class={terrain_class}")
        if base < 0:  # -1 = 不可通行
            return None
        factor = self.slope_penalty.get(profile, 0.0)
        slope = min(max(slope_deg, 0.0), _SLOPE_REF_DEG)
        return base * (1.0 + factor * slope / _SLOPE_REF_DEG)

    def min_step_cost(self, profile: str) -> float:
        """該 profile 任一可通行 cell 的成本下界（slope=0、取最小 base）——A* heuristic 用，
        必須是真正的下界才能保持 admissible。全不可通行時回 0（heuristic 退化為 0，仍安全）。
        """
        positives = [base for base in self.profiles[profile].values() if base > 0]
        return min(positives) if positives else 0.0
