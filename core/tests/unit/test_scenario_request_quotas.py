"""想定層申請配額（WP-B5.2）——宣告 → 開局快照。"""

from __future__ import annotations

import json
from pathlib import Path

import jsonschema
import pytest

SCHEMA = json.loads((Path(__file__).parents[3] / "contracts" / "scenario.schema.json").read_text())


def _minimal(**extra: object) -> dict[str, object]:
    base: dict[str, object] = {
        "name": "t",
        "version": "1.0",
        "bbox": [120.0, 23.0, 121.0, 24.0],
        "mode": "REALTIME",
        "tick_rate_ms": 1000,
        "factions": [{"id": "BLUE"}, {"id": "RED"}],
        "victory_conditions": [
            {"faction": "BLUE", "condition": {"type": "faction_eliminated", "faction": "RED"}}
        ],
    }
    base.update(extra)
    return base


def test_request_quotas_accepted() -> None:
    jsonschema.validate(
        _minimal(request_quotas={"AIR_RECON": 4, "FIRE_SUPPORT": 10}),
        SCHEMA,
    )


def test_request_quotas_optional() -> None:
    """未宣告＝不限（既有想定零變更）。"""
    jsonschema.validate(_minimal(), SCHEMA)


def test_unknown_request_kind_rejected() -> None:
    """打錯申請種類要當場擋——想定照樣載入、配額卻悄悄失效是 O7.1 踩過的坑。"""
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_minimal(request_quotas={"AIR_STRIKE": 3}), SCHEMA)


def test_negative_quota_rejected() -> None:
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(_minimal(request_quotas={"AIR_RECON": -1}), SCHEMA)
