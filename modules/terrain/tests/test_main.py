"""Terrain 服務進入點測試（O2.5）：serve 以 mock 取代（避免阻塞），驗證組裝流程。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from terrain import __main__


def test_main_builds_plugin_and_serves(tmp_path: Path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # 指向不存在的資產 → build_from_settings 回 DOWN 插件，但 main 仍應正常組裝並呼叫 serve
    monkeypatch.setenv("MATSO_DTED_PATH", str(tmp_path / "nope.tif"))
    monkeypatch.setenv("MATSO_HEX_CACHE_DIR", str(tmp_path / "empty"))
    with patch.object(__main__, "serve") as serve_mock:
        __main__.main(["--port", "0", "--res", "7"])
    serve_mock.assert_called_once()
    # serve(plugin, host=..., port=0)
    _, kwargs = serve_mock.call_args
    assert kwargs["port"] == 0
