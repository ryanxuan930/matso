"""Comms 進入點（O5.4）：serve 以 mock 取代，驗證組裝。"""

from __future__ import annotations

from unittest.mock import patch

from comms.plugin import CommsPlugin

from comms import __main__


def test_main_serves() -> None:
    with patch.object(__main__, "serve") as serve_mock:
        __main__.main(["--port", "0"])
    serve_mock.assert_called_once()
    assert isinstance(serve_mock.call_args[0][0], CommsPlugin)
