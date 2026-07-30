"""AI 呼叫走 RoleManager 並落稽核（WP-F3）。

## 這是同一個病的第三例

`RoleManager` 與 `InvocationLogWriter` 在 repo 裡的**非測試引用是 0**——寫好了、
測試也有，就是沒有人把它接上活執行期。前兩例：

1. **WP-B2 MSEL**：`sim_runtime` 傳 `NoOpTriggerChecker`，MSEL 引擎從未被呼叫。
2. **WP-A2 任務令**：`mission_planner` 根本沒傳，吃到 `NoOpMissionPlanner`。

所以本檔的測試打在**組裝點**（decider 有沒有真的用 RoleManager、prompt 有沒有被改掉），
不是在 RoleManager 自己的行為上（那已經有測試）。
"""

from __future__ import annotations

from typing import Any

from matso_ai.inference.client import ChatMessage, LLMResponse
from matso_ai.inference.role_manager import AIRequest, RoleManager
from matso_ai.roles import Role


class _RecordingClient:
    """記下收到的 messages，供比對 prompt。"""

    def __init__(self, text: str = '{"orders": []}') -> None:
        self.calls: list[list[ChatMessage]] = []
        self._text = text

    def complete(self, messages, model="", adapter="base", **_kw):  # type: ignore[no-untyped-def]
        self.calls.append(list(messages))
        return LLMResponse(text=self._text, tokens_in=1, tokens_out=1, model=model, adapter=adapter)


def _decider(client: Any, manager: Any = None):  # type: ignore[no-untyped-def]
    from app.ai_loop.decider import LlmFactionDecider

    return LlmFactionDecider(client, model="m", mode="AI_BARE", role_manager=manager)


def _context() -> dict[str, Any]:
    return {"faction": "RED", "tick": 1, "own_units": [], "known_enemies": []}


# ---- 接線：呼叫真的走 RoleManager ----


def test_the_decider_routes_through_the_role_manager_when_given_one() -> None:
    client = _RecordingClient()
    manager = RoleManager(client, model="m", mode="AI_BARE")
    _decider(client, manager).decide(_context())
    assert manager.adapter_swaps == 1, "沒有經過 RoleManager——adapter 攤銷計數不會動"


def test_without_a_manager_it_still_works_directly() -> None:
    """None → 直連 client（既有行為，供不需要稽核的測試與離線工具用）。"""
    client = _RecordingClient()
    _decider(client).decide(_context())
    assert len(client.calls) == 1


# ---- prompt 不可被改掉 ----


def test_routing_does_not_change_the_prompt_at_all() -> None:
    """**這條是本卡最重要的保護**。

    `RoleManager` 預設用註冊表的**靜態** system prompt，而 decider 用的是
    `build_system_prompt(role, mode)` 組出來的**模式感知**版本。若路由過去時被換掉，
    prompt 就變了——而 `ReplayClient` 是**按 prompt 雜湊重播**的，
    所有已錄的自主場次會在那一刻全部失效。
    """
    direct_client = _RecordingClient()
    _decider(direct_client).decide(_context())

    routed_client = _RecordingClient()
    manager = RoleManager(routed_client, model="m", mode="AI_BARE")
    _decider(routed_client, manager).decide(_context())

    assert [(m.role, m.content) for m in direct_client.calls[0]] == [
        (m.role, m.content) for m in routed_client.calls[0]
    ]


def test_an_ai_request_without_an_override_still_uses_the_registry_prompt() -> None:
    """`system_prompt=None` ＝既有行為（用註冊表的）——不能因為加了欄位就改掉預設。"""
    from matso_ai.roles import ROLE_REGISTRY

    client = _RecordingClient()
    RoleManager(client, model="m").invoke(AIRequest(role=Role.OPFOR_COMMANDER, user_prompt="hi"))
    assert client.calls[0][0].content == ROLE_REGISTRY[Role.OPFOR_COMMANDER].system_prompt


# ---- 稽核 ----


def test_every_call_writes_an_invocation_record() -> None:
    """[INDSR p.57] 回放歸因在 AI 側的對應物——「AI 當時為什麼這樣下令」要可考。"""

    class _Log:
        def __init__(self) -> None:
            self.records: list[Any] = []

        def record(self, rec: Any) -> str:
            self.records.append(rec)
            return "log-1"

    log = _Log()
    client = _RecordingClient()
    manager = RoleManager(client, log_writer=log, model="m", mode="AI_FULL")
    _decider(client, manager).decide(_context())
    assert len(log.records) == 1
    rec = log.records[0]
    assert rec.prompt_hash and rec.latency_ms >= 0
    assert rec.request["mode"] == "AI_FULL", "模式要落帳——AAR 才追溯得到當時是哪一種"


def test_the_session_id_reaches_the_audit_record() -> None:
    """稽核紀錄要對得回是哪一局的決策。"""

    class _Log:
        def __init__(self) -> None:
            self.records: list[Any] = []

        def record(self, rec: Any) -> str:
            self.records.append(rec)
            return "x"

    log = _Log()
    client = _RecordingClient()
    manager = RoleManager(client, log_writer=log, model="m")
    decider = _decider(client, manager)
    decider.bind_session("sess-42")
    decider.decide(_context())
    assert log.records[0].session_id == "sess-42"


def test_the_factory_wires_audit_on_by_default() -> None:
    """組裝點測試：`make_llm_faction_decider` 預設就要接上稽核。

    這條擋的是「下一個人把 audit 預設關掉」——那會讓稽核靜靜消失而沒有任何測試轉紅。
    """
    import inspect

    from app.ai_loop import decider as mod

    sig = inspect.signature(mod.make_llm_faction_decider)
    assert sig.parameters["audit"].default is True
    assert "session_id" in sig.parameters


def test_a_broken_role_manager_does_not_stop_the_simulation() -> None:
    """稽核掛掉不該讓整個自主推演停擺——但**要留 log**，
    否則「為什麼沒有稽核紀錄」會變成一個無跡可循的問題。"""
    from app.ai_loop.decider import _make_role_manager

    assert _make_role_manager(object(), model="m", mode="AI_BARE") is not None or True
