# ADR 004：CI 不使用 prisma migrate diff 作為 drift guard

日期：2026-07-18　狀態：Accepted

## Context
原想在 CI 以 `prisma migrate diff --from-migrations --to-schema-datamodel --exit-code`
確保 schema.prisma 與 migrations 同步。實測發現 MariaDB 的 JSON 型別底層是
LONGTEXT + CHECK constraint，導致 diff 對「所有 Json 欄位」永久誤報 type changed。

## Decision
CI 的 schema-sync job 只做：(1) `prisma migrate deploy` 於乾淨 DB 驗證 migrations 可套用；
(2) `ops/tools/schema_sync_check.py` 比對 SQLAlchemy models 與 schema.prisma。
不使用 migrate diff guard。

## Consequences
- 「改了 schema.prisma 卻忘了產 migration」的情境不會被 diff 抓到；
  緩解：M0-4 之後所有 schema 變更 PR 的 checklist 要求附 migration 檔（HOW_TO §5 卡片驗收）。
- 若未來 Prisma 修復 MariaDB JSON 誤報（或改用 MySQL 8），可重新評估。
