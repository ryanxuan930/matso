"""系統設定 REST（#54）——全系統通用參數（AI 模式 / LLM 後端 / 系統資訊）。

編輯限統裁/白軍/管理（is_omniscient）。設定存於既有 `SystemConfiguration` 表的
`integrationConfig.ai`（免 migration）。ENV/容器層參數（gRPC 位址、redis、DTED 路徑）於此
唯讀檢視——那些由容器掛載/啟動 ENV 決定，改了需重啟對應服務才生效。

GET  /api/v1/system/config           檢視（可編輯 AI/LLM 設定 + 唯讀系統資訊）
PUT  /api/v1/system/config           更新 AI/LLM 設定
POST /api/v1/system/config/test-llm  測試 LLM 後端連線（如 Ollama：POST {base}/v1/chat/completions）

註：AI 決策迴路（run_opfor_turn + 護欄）尚未接入活執行期 Kernel，故此設定目前供「連線測試 +
未來 AI 推演」使用；活模擬尚不會據此自動下令（見 SPEC_EXTEND / autonomous-ai 規劃）。
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.deps import get_current_user, get_db, get_settings
from app.auth.schemas import CurrentUser
from app.config import Settings
from app.errors import AuthForbiddenError, OrderValidationError
from app.models.enums import AiMode
from app.models.tables import SystemConfiguration
from app.stream.faction_filter import is_omniscient
from matso_ai.inference.client import chat_completions_url

router = APIRouter(prefix="/api/v1/system", tags=["system"])

_AI_MODES = [m.value for m in AiMode]  # AI_OFF / AI_BARE / AI_FULL


def _require_admin(user: CurrentUser) -> None:
    if not is_omniscient(user.role):
        raise AuthForbiddenError("僅統裁/白軍/管理可管理系統設定")


def _mask_url(url: str) -> str:
    """遮罩連線字串中的帳密（redis://user:pass@host → redis://***@host）。"""
    if "@" in url and "://" in url:
        scheme, rest = url.split("://", 1)
        _creds, host = rest.split("@", 1)
        return f"{scheme}://***@{host}"
    return url


def _singleton(db: Session) -> SystemConfiguration:
    cfg = db.execute(select(SystemConfiguration).limit(1)).scalars().first()
    if cfg is None:
        cfg = SystemConfiguration(
            version_name="default",
            sim_tick_rate_ms=1000,
            global_rules={},
            integration_config={},
            updated_at=datetime.now(),
        )
        db.add(cfg)
        db.commit()
    return cfg


def _ai_cfg(cfg: SystemConfiguration) -> dict[str, Any]:
    ic = cfg.integration_config or {}
    ai = ic.get("ai") if isinstance(ic, dict) else None
    return ai if isinstance(ai, dict) else {}


class AiConfigEdit(BaseModel):
    ai_mode: str
    llm_base_url: str = ""
    llm_model: str = ""
    # None＝不變（避免每次都要重打 key）；空字串＝清除。
    llm_api_key: str | None = None


class SystemConfigView(BaseModel):
    ai: dict[str, Any]
    readonly: dict[str, Any]


def _view(db: Session, settings: Settings) -> SystemConfigView:
    ai = _ai_cfg(_singleton(db))
    return SystemConfigView(
        ai={
            "ai_mode": ai.get("mode") or settings.ai_mode,
            "llm_base_url": ai.get("llm_base_url") or os.environ.get("OPENAI_BASE_URL", ""),
            "llm_model": ai.get("llm_model") or os.environ.get("MATSO_LLM_MODEL", ""),
            "llm_api_key_set": bool(ai.get("llm_api_key") or os.environ.get("OPENAI_API_KEY", "")),
            "ai_modes": _AI_MODES,
        },
        readonly={
            "env": settings.matso_env,
            "ai_mode_env_default": settings.ai_mode,
            "terrain_grpc_target": settings.terrain_grpc_target,
            "weather_grpc_target": settings.weather_grpc_target,
            "redis_url": _mask_url(settings.redis_url),
            "stub_gateway": settings.stub_gateway,
            # AI 決策迴路是否已接入活執行期（目前未接；設定僅供連線測試/未來推演）。
            "ai_loop_wired": False,
        },
    )


@router.get("/config", response_model=SystemConfigView)
def get_config(
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SystemConfigView:
    _require_admin(user)
    return _view(db, settings)


@router.put("/config", response_model=SystemConfigView)
def put_config(
    edit: AiConfigEdit,
    user: CurrentUser = Depends(get_current_user),
    db: Session = Depends(get_db),
    settings: Settings = Depends(get_settings),
) -> SystemConfigView:
    _require_admin(user)
    if edit.ai_mode not in _AI_MODES:
        raise OrderValidationError(
            f"未知 AI 模式：{edit.ai_mode}", error_code="SYSTEM_INVALID_AI_MODE"
        )
    cfg = _singleton(db)
    ic = dict(cfg.integration_config or {})
    ai = dict(ic.get("ai") or {}) if isinstance(ic.get("ai"), dict) else {}
    ai["mode"] = edit.ai_mode
    ai["llm_base_url"] = edit.llm_base_url.strip().rstrip("/")
    ai["llm_model"] = edit.llm_model.strip()
    if edit.llm_api_key is not None:  # None＝保留原值；"" ＝清除
        ai["llm_api_key"] = edit.llm_api_key
    ic["ai"] = ai
    cfg.integration_config = ic
    cfg.updated_at = datetime.now()  # type: ignore[assignment]
    db.commit()
    return _view(db, settings)


class TestLlmRequest(BaseModel):
    base_url: str
    model: str
    api_key: str | None = None


class TestLlmResult(BaseModel):
    ok: bool
    detail: str
    latency_ms: int | None = None


@router.post("/config/test-llm", response_model=TestLlmResult)
def test_llm(
    req: TestLlmRequest,
    user: CurrentUser = Depends(get_current_user),
) -> TestLlmResult:
    """測 OpenAI 相容 LLM 後端（Ollama：base=http://host.docker.internal:11434）連線 + 回應。

    走與真 client 同一路徑 `{base}/v1/chat/completions`——確認 base_url/model 可通。
    """
    _require_admin(user)
    base = req.base_url.strip().rstrip("/")
    if not base:
        return TestLlmResult(ok=False, detail="Base URL 未填")
    if not req.model.strip():
        return TestLlmResult(ok=False, detail="Model 未填")
    payload: dict[str, Any] = {
        "model": req.model.strip(),
        "messages": [{"role": "user", "content": "ping"}],
        "stream": False,
    }
    headers = {"Content-Type": "application/json"}
    if req.api_key:
        headers["Authorization"] = f"Bearer {req.api_key}"
    # base_url 由 admin 填入（受 admin gate 保護）；端點路徑與真 client 同源（Ollama→/v1/…、
    # Google AI Studio 等已含路徑者→/chat/completions），確保「測試連線」與實際呼叫一致。
    request = urllib.request.Request(
        chat_completions_url(base),
        data=json.dumps(payload).encode(),
        headers=headers,
        method="POST",
    )
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=45.0) as resp:
            raw = resp.read()
        latency = int((time.perf_counter() - started) * 1000)
        data = json.loads(raw)
        text = str(((data.get("choices") or [{}])[0].get("message") or {}).get("content", ""))[:80]
        return TestLlmResult(
            ok=True, detail=f"連線成功（{req.model}）｜回應：{text}", latency_ms=latency
        )
    except urllib.error.HTTPError as exc:
        body = exc.read().decode(errors="replace")[:200]
        return TestLlmResult(ok=False, detail=f"HTTP {exc.code}：{body}")
    except (urllib.error.URLError, TimeoutError, ValueError, KeyError) as exc:
        return TestLlmResult(ok=False, detail=f"連線失敗：{exc}")
