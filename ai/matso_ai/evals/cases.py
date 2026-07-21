"""評測案例載入與驗證（對 ai/evals/case.schema.json）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator

_EVALS_DIR = Path(__file__).resolve().parents[2] / "evals"
CASES_DIR = _EVALS_DIR / "cases"
_SCHEMA = _EVALS_DIR / "case.schema.json"


def _validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(_SCHEMA.read_text(encoding="utf-8")))


def load_cases(cases_dir: Path | None = None) -> list[dict[str, Any]]:
    """載入並驗證所有 cases/*.yaml。案例不符 schema → ValueError（含檔名與路徑）。空目錄→[]。"""
    directory = cases_dir or CASES_DIR
    validator = _validator()
    cases: list[dict[str, Any]] = []
    for path in sorted(directory.glob("*.yaml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        errors = sorted(validator.iter_errors(data), key=lambda e: list(e.path))
        if errors:
            first = errors[0]
            raise ValueError(f"{path.name} 不符 case.schema：{list(first.path)}: {first.message}")
        cases.append(data)
    return cases
