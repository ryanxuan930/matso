# ADR 001：uv workspace root 放在 repo root

日期：2026-07-18　狀態：Accepted

## Context
Python 程式碼分散在 core/、modules/*、ai/。需要決定 uv workspace root 的位置與 venv 策略。

## Decision
Workspace root = repo root 的 `pyproject.toml`（`package = false` 虛擬 root）。
members：core、modules/_sdk、modules/terrain、modules/weather、ai。
所有 member 以 dev dependency + `[tool.uv.sources] workspace = true` editable 安裝進單一 root venv。
ruff / mypy / pytest 設定集中在 root，`uv run <cmd>` 一律在 repo root 執行。

## Consequences
- 單一 venv、單一 lockfile（uv.lock），CI 只需一次 `uv sync --frozen`。
- 各 module 的重依賴（rasterio/GDAL 等）加入時會影響整個 venv 的 sync 時間——屆時若不可接受，
  可改為 per-member `uv sync --package` 的分離策略（Docker 映像已採此法：`--package matso-core`）。
