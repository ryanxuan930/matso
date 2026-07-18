# Architecture Decision Records

格式：`NNN-title.md`，三段即可 — Context / Decision / Consequences（HOW_TO.md §7.4）。

| # | 標題 | 狀態 |
|---|------|------|
| [001](001-uv-workspace-root.md) | uv workspace root 放在 repo root | Accepted |
| [002](002-checkpoint-blob-storage.md) | SimCheckpoint 快照 inline LONGBLOB + 8MB 護欄 | Accepted |
| [003](003-npm-not-pnpm.md) | 前端與 db 沿用 npm，不引入 pnpm | Accepted |
| [004](004-no-prisma-migrate-diff-guard.md) | CI 不使用 prisma migrate diff 作為 drift guard | Accepted |
