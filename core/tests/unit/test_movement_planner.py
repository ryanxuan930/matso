"""TerrainClientPlanner 轉接（O3.4）：包裝 terrain gRPC client 的 GetPath。"""

from __future__ import annotations

from types import SimpleNamespace

from app.movement.planner import TerrainClientPlanner


class _FakeClient:
    def __init__(self, reachable: bool, path: list[str]) -> None:
        self._reachable = reachable
        self._path = path
        self.calls: list[tuple[str, str, str]] = []

    def get_path(self, from_h3: str, to_h3: str, mobility_profile: str) -> object:
        self.calls.append((from_h3, to_h3, mobility_profile))
        return SimpleNamespace(reachable=self._reachable, h3_path=self._path)


def test_planner_returns_path_when_reachable() -> None:
    client = _FakeClient(True, ["a", "b", "c"])
    planner = TerrainClientPlanner(client)
    assert planner.plan("a", "c", "FOOT") == ["a", "b", "c"]
    assert client.calls == [("a", "c", "FOOT")]


def test_planner_returns_empty_when_unreachable() -> None:
    planner = TerrainClientPlanner(_FakeClient(False, []))
    assert planner.plan("a", "z", "WHEELED") == []
