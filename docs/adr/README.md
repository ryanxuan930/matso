# Architecture Decision Records

格式：`NNN-title.md`，三段即可 — Context / Decision / Consequences（HOW_TO.md §7.4）。

| # | 標題 | 狀態 |
|---|------|------|
| [001](001-uv-workspace-root.md) | uv workspace root 放在 repo root | Accepted |
| [002](002-checkpoint-blob-storage.md) | SimCheckpoint 快照 inline LONGBLOB + 8MB 護欄 | Accepted |
| [003](003-npm-not-pnpm.md) | 前端與 db 沿用 npm，不引入 pnpm | Accepted |
| [004](004-no-prisma-migrate-diff-guard.md) | CI 不使用 prisma migrate diff 作為 drift guard | Accepted |
| [005](005-offline-proto-codegen.md) | gRPC codegen 用 grpcio-tools（離線），產物不入 git | Accepted |
| [006](006-n-faction-relations-matrix.md) | N 方對抗與陣營關係矩陣 | Accepted |
| [007](007-rollback-logical-truncation.md) | Rollback 的 Ledger 語義：世代標記，不做實體截斷 | Accepted |
