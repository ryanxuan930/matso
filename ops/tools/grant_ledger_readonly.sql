-- Event Ledger append-only 的 DB 權限層防線（SPEC_FULL §15.3）
-- ---------------------------------------------------------------------------
-- 目的：對 MATSO 應用程式帳號硬性禁止 UPDATE / DELETE TacticalEventLog，
--       使「不可變帳本」不只靠應用層（LedgerWriter 無 update/delete 方法），
--       在 DB 權限層亦成立——即使程式被入侵或有 bug，也無法竄改歷史事件。
--
-- 前提：正式環境應為 MATSO 建立**專用的非 root 應用帳號**（例如 'matso_app'），
--       Core 以該帳號連線。root/DBA 帳號不受此限（用於維運與 prisma migrate）。
--
-- 使用：
--   1. 依實際環境調整下方帳號名稱與來源主機（'matso_app'@'%'）。
--   2. 以具 GRANT 權限的帳號執行：
--        mariadb -uroot -p matso < ops/tools/grant_ledger_readonly.sql
--
-- 注意：本機 compose 開發環境使用 root（無法有意義地自我限制），故不套用此腳本；
--       開發期的 append-only 由應用層（LedgerWriter 無 update/delete）保證。
-- ---------------------------------------------------------------------------

-- Kernel 需要對帳本 INSERT 與 SELECT（讀鏈尾、重播、AAR）：
GRANT SELECT, INSERT ON matso.TacticalEventLog TO 'matso_app'@'%';

-- 收回竄改能力：
REVOKE UPDATE, DELETE ON matso.TacticalEventLog FROM 'matso_app'@'%';

FLUSH PRIVILEGES;
