"""WebSocket 串流端點（O4.3，SPEC §16.2、contracts/ws_protocol.md）。

WS /api/v1/sessions/{id}/stream?token=<jwt>
- token 認證（access JWT）+ faction-scope 解析（非參與者且非全知 → 拒絕）。
- HELLO{last_seq} → 範圍檢查（O1.7/R7）：補送 ring 缺漏 / 已最新 / RESYNC_REQUIRED。
- pub-sub 轉發（faction 過濾 + 背壓有界佇列；慢 client 溢出 → 斷線要求重同步）。

Redis 讀取用 redis.asyncio（ring lrange + pub-sub）；廣播寫入仍是 Kernel 端 sync RedisBroadcaster。
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import Any

import redis.asyncio as aioredis
from fastapi import APIRouter, Depends, WebSocket, WebSocketDisconnect
from sqlalchemy.orm import Session

from app.api.deps import get_auth_service, get_db, get_settings
from app.auth.service import AuthService
from app.config import Settings
from app.errors import MatsoError
from app.stream.backfill import plan_resume, select_backfill, seq_range
from app.stream.faction_filter import is_visible
from app.stream.identity import WsIdentity, resolve_ws_identity
from app.stream.sender import BackpressureError, BoundedSender

_LOG = logging.getLogger("app.ws")

router = APIRouter(prefix="/api/v1/sessions", tags=["stream"])

# WebSocket close codes（RFC 6455 應用區間 4xxx）
CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN = 4403
CLOSE_BACKPRESSURE = 4408  # 慢 client：斷線並要求重同步


def _ring_key(session_id: str) -> str:
    return f"session:{session_id}:ring"


def _channel(session_id: str) -> str:
    return f"session:{session_id}:stream"


@router.websocket("/{session_id}/stream")
async def session_stream(
    websocket: WebSocket,
    session_id: str,
    token: str = "",
    db: Session = Depends(get_db),
    auth: AuthService = Depends(get_auth_service),
    settings: Settings = Depends(get_settings),
) -> None:
    # 1) 認證 + faction 解析（accept 前先驗，失敗即拒）
    identity = _authenticate(db, auth, session_id, token)
    if identity is None:
        await websocket.close(code=CLOSE_UNAUTHORIZED)
        return

    await websocket.accept()
    redis = aioredis.from_url(settings.redis_url, decode_responses=True)
    try:
        await _run_stream(websocket, redis, session_id, identity)
    except WebSocketDisconnect:
        pass
    finally:
        await redis.aclose()


def _authenticate(db: Session, auth: AuthService, session_id: str, token: str) -> WsIdentity | None:
    if not token:
        return None
    try:
        user = auth.current_user(token)
    except MatsoError:
        return None
    return resolve_ws_identity(db, user.id, user.role, session_id)


async def _run_stream(
    websocket: WebSocket, redis: aioredis.Redis, session_id: str, identity: WsIdentity
) -> None:
    # 2) HELLO{last_seq}
    hello = await websocket.receive_json()
    last_seq = _parse_last_seq(hello)

    # 3) 先訂閱 pub-sub（開始緩衝 live 訊息）→ 再讀 ring 快照補送（CODE_REVIEW C2）。
    #    反過來（先讀 ring 再訂閱）會漏掉「快照後、訂閱前」發佈的事件——client 無感遺失。
    sender = BoundedSender()
    pubsub = redis.pubsub()
    await pubsub.subscribe(_channel(session_id))
    try:
        # 4) 讀 ring → 範圍檢查 → WELCOME / RESYNC_REQUIRED + 補送
        envelopes = await _read_ring(redis, session_id)
        ring_min, ring_max = seq_range(envelopes)
        plan = plan_resume(ring_min, ring_max, last_seq)

        if plan.resync:
            await websocket.send_json(
                {"v": 1, "type": "RESYNC_REQUIRED", "payload": {"reason": "last_seq out of range"}}
            )
        await websocket.send_json(
            {
                "v": 1,
                "type": "WELCOME",
                "payload": {
                    "session": session_id,
                    "faction": identity.faction,
                    "resumed_from_seq": plan.resumed_from_seq,
                },
            }
        )
        # 已補送的最大 seq——live 迴圈用它去重「訂閱緩衝與 backfill 重疊」的訊息。
        sent_through = plan.resumed_from_seq
        if plan.backfill_after_seq is not None:
            for env in select_backfill(envelopes, plan.backfill_after_seq):
                if is_visible(env, identity.faction, identity.omniscient):
                    await websocket.send_json(env)
                    sent_through = max(sent_through, int(env.get("seq", sent_through)))

        # 5) live pub-sub 轉發（faction 過濾 + 背壓 + seq 去重）
        await _pump_live(websocket, pubsub, sender, session_id, identity, sent_through)
    finally:
        await pubsub.unsubscribe(_channel(session_id))
        await pubsub.aclose()


def _parse_last_seq(hello: Any) -> int | None:
    """HELLO.last_seq 的健壯解析（CODE_REVIEW C6）：非 int（含 bool/字串/缺失）一律當 None。"""
    if not isinstance(hello, dict):
        return None
    value = hello.get("last_seq")
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


async def _read_ring(redis: aioredis.Redis, session_id: str) -> list[dict[str, Any]]:
    raw = await redis.lrange(_ring_key(session_id), 0, -1)
    out: list[dict[str, Any]] = []
    for item in raw:
        with contextlib.suppress(json.JSONDecodeError, TypeError):
            out.append(json.loads(item))
    return out


async def _pump_live(
    websocket: WebSocket,
    pubsub: Any,
    sender: BoundedSender,
    session_id: str,
    identity: WsIdentity,
    sent_through: int,
) -> None:
    """轉發已訂閱頻道的 live 訊息。pubsub 由呼叫端先訂閱好（C2）；跳過 seq ≤ sent_through 者
    （與 backfill 重疊的去重）。"""

    async def produce() -> None:
        async for message in pubsub.listen():
            if message.get("type") != "message":
                continue
            try:
                env = json.loads(message["data"])
            except (json.JSONDecodeError, TypeError):
                continue
            seq = env.get("seq")
            if isinstance(seq, int) and seq <= sent_through:
                continue  # 已於 backfill 送過（訂閱緩衝與快照重疊）
            if is_visible(env, identity.faction, identity.omniscient):
                sender.offer(env)  # 慢 client → BackpressureError

    async def consume() -> None:
        while True:
            env = await sender.next()
            await websocket.send_json(env)

    producer = asyncio.create_task(produce())
    consumer = asyncio.create_task(consume())
    try:
        done, _pending = await asyncio.wait(
            {producer, consumer}, return_when=asyncio.FIRST_EXCEPTION
        )
        for task in done:
            exc = task.exception()
            if isinstance(exc, BackpressureError):
                _LOG.warning("session %s: 慢 client 背壓斷線", session_id)
                await websocket.close(code=CLOSE_BACKPRESSURE)
            elif exc is not None and not isinstance(exc, WebSocketDisconnect):
                raise exc
    finally:
        producer.cancel()
        consumer.cancel()
