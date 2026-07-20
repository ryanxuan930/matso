"""MATSO plugin SDK — MatsoPlugin base class + gRPC server 樣板 + 整合測試 harness（SPEC §17）。

gRPC stubs 於 `matso_sdk._generated`（不入 git，`ops/tools/gen_proto.py` 產生）。
"""

from matso_sdk.harness import PluginHarness, run_plugin
from matso_sdk.health import HealthState, from_proto, to_proto
from matso_sdk.manifest import Manifest, PluginKind
from matso_sdk.plugin import MatsoPlugin
from matso_sdk.server import build_server, serve

__version__ = "0.1.0"

__all__ = [
    "HealthState",
    "Manifest",
    "MatsoPlugin",
    "PluginHarness",
    "PluginKind",
    "__version__",
    "build_server",
    "from_proto",
    "run_plugin",
    "serve",
    "to_proto",
]
