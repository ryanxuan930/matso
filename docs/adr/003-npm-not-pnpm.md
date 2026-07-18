# ADR 003：前端與 db 沿用 npm，不引入 pnpm

日期：2026-07-18　狀態：Accepted

## Context
HOW_TO 初稿指定 pnpm 9+，但 `platform/` 由使用者以 npm 建立（已有 package-lock.json），
且開發機未安裝 pnpm。

## Decision
沿用 npm（>=11）。db/ 的 prisma 工具鏈同樣用 npm。HOW_TO 已同步修正。

## Consequences
- 少一個全域工具依賴；lockfile 維持既有格式。
- 注意：node:22-alpine 內建 npm 10 對 npm 11 產生的 lock 會 EUSAGE 失敗，
  platform/Dockerfile 已在 `npm ci` 前升級 npm（見該檔註解）。
