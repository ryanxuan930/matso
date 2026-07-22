"""想定持久化 REST（#7，SPEC §11）——存 / 列想定（限統裁/管理）。

POST /api/v1/scenarios  存編輯器 bundle（存前全量驗證）
GET  /api/v1/scenarios  列出已存想定

開局：lobby `create_session` 帶 `scenario_id` → 載回 bundle → 建 session + 單位。
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db
from app.auth.schemas import CurrentUser
from app.errors import AuthForbiddenError, ScenarioInvalidError
from app.models import Scenario
from app.scenario import ScenarioError, load_scenario_bundle
from app.stream.faction_filter import is_omniscient

router = APIRouter(prefix="/api/v1/scenarios", tags=["scenarios"])


class ScenarioBundle(BaseModel):
    scenario: dict[str, Any]
    orbat: dict[str, Any] = Field(default_factory=dict)
    msel: dict[str, Any] | None = None


class ScenarioSaved(BaseModel):
    id: str
    name: str
    version: str


def _require_editor(user: CurrentUser) -> None:
    """想定編輯/存取限統裁/管理角色（SPEC §11.2 / §12）。"""
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅統裁/管理角色可存取想定")


@router.post("", response_model=ScenarioSaved)
def save_scenario(
    bundle: ScenarioBundle,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> ScenarioSaved:
    _require_editor(user)
    data = bundle.model_dump()
    try:
        loaded = load_scenario_bundle(data)  # 存前全量驗證（精確錯誤路徑）
    except ScenarioError as exc:
        raise ScenarioInvalidError(str(exc)) from exc
    blob = json.dumps(data, ensure_ascii=False, sort_keys=True).encode("utf-8")
    row = Scenario(
        name=loaded.name,
        version=loaded.version,
        package_blob=blob,
        checksum=hashlib.sha256(blob).hexdigest(),
        created_by=user.id,
    )
    db.add(row)
    db.commit()
    return ScenarioSaved(id=row.id, name=row.name, version=row.version)


@router.get("", response_model=list[ScenarioSaved])
def list_scenarios(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> list[ScenarioSaved]:
    _require_editor(user)
    rows = db.execute(select(Scenario).order_by(Scenario.created_at.desc())).scalars().all()
    return [ScenarioSaved(id=r.id, name=r.name, version=r.version) for r in rows]
