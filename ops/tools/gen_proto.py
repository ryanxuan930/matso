"""從 contracts/proto 產生 Python gRPC stubs（O2.5）。

**離線 codegen**：用 `grpc_tools.protoc`（純 Python、無網路），而非 buf remote plugins——
SPEC §18 要求 air-gapped 部署，remote plugins 需連網、local plugins 需另裝 protoc-gen 二進位
（決策見 docs/adr/005）。產物落在 `matso_sdk/_generated/`（**不入 git**，見 .gitignore）。

用法：`uv run python ops/tools/gen_proto.py`（CI、Dockerfile、開發前都跑一次）。

匯入方式（消費端）：
    from matso_sdk._generated import plugin_base_pb2, plugin_base_pb2_grpc
    from matso_sdk._generated import terrain_pb2, terrain_pb2_grpc
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "modules" / "_sdk" / "matso_sdk" / "_generated"
PROTOS = [
    ROOT / "contracts" / "proto" / "matso" / "plugin" / "v1" / "plugin_base.proto",
    ROOT / "contracts" / "proto" / "matso" / "terrain" / "v1" / "terrain.proto",
    ROOT / "contracts" / "proto" / "matso" / "weather" / "v1" / "weather.proto",
]

_INIT_DOC = (
    '"""Generated gRPC stubs — NOT in git. Regenerate via `ops/tools/gen_proto.py`.\n\n'
    "來源契約：contracts/proto/matso/**/v1/*.proto（O2.5）。\n"
    '"""\n'
)


def generate() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "__init__.py").write_text(_INIT_DOC, encoding="utf-8")

    # 每個 proto 的所在目錄各作為 -I 根 → 產出扁平檔名（plugin_base_pb2.py 等）
    args = [sys.executable, "-m", "grpc_tools.protoc"]
    for proto in PROTOS:
        args += ["-I", str(proto.parent)]
    args += [f"--python_out={OUT}", f"--grpc_python_out={OUT}", f"--pyi_out={OUT}"]
    args += [proto.name for proto in PROTOS]
    subprocess.run(args, check=True)

    _fix_imports()


def _fix_imports() -> None:
    """把扁平產出的絕對匯入改為套件相對匯入。

    grpc_tools 產的 `*_pb2_grpc.py` 內含 `import terrain_pb2 as ...`（扁平根假設），
    在套件內須改為 `from . import terrain_pb2 as ...` 才能 import。
    """
    pattern = re.compile(r"^import (\w+_pb2) as", re.MULTILINE)
    for path in OUT.glob("*_pb2_grpc.py"):
        text = path.read_text(encoding="utf-8")
        fixed = pattern.sub(r"from . import \1 as", text)
        if fixed != text:
            path.write_text(fixed, encoding="utf-8")


if __name__ == "__main__":
    generate()
    rel = OUT.relative_to(ROOT)
    print(f"✓ gRPC stubs 產生於 {rel}（{len(list(OUT.glob('*.py')))} 檔）")
