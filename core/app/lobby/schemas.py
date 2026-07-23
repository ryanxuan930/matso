"""Lobby REST 載荷（O4.1）——對應 core_api.yaml 的 SessionSummary / CreateSessionRequest。"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.models.enums import SessionMode


class CreateSessionRequest(BaseModel):
    name: str = Field(min_length=1)
    scenario_id: str | None = None
    mode: SessionMode = SessionMode.REALTIME


class EditSessionRequest(BaseModel):
    """編輯已開推演設定（#16）——名稱 / 想定世界初始日期時間（ISO8601）。"""

    name: str | None = Field(default=None, min_length=1)
    world_start_time: str | None = None  # ISO8601；空字串視為清除


class SessionSummary(BaseModel):
    id: str
    name: str
    scenario_id: str | None
    mode: str
    status: str  # ACTIVE / ENDED / ARCHIVED（由 end_time / archived_at 推導，#31）
    my_faction: str | None  # 呼叫者在此 session 的陣營（非參與者為 null）
    start_time: str | None = None  # 開局時間 ISO8601（供 COP 顯示執行時間，#4）
    world_start_time: str | None = None  # 想定世界初始日期時間（#16/#6）
    archived_at: str | None = None  # 封存時間 ISO8601（#31；有值＝已封存）
    orbat_edit: bool = False  # 呼叫者是否可編輯本 session 編裝（白軍，或本軍且該局開放自編）
