-- AlterTable
-- 兵科（APP-6A/2525C function ID 的來源）。決定地圖符號畫成步兵斜線／裝甲橢圓／砲兵圓點…
--
-- **NOT NULL + DEFAULT 'UNKNOWN'** 是刻意的中性預設：既有的每一列都會拿到 UNKNOWN，
-- 而 UNKNOWN 在前端對應通用框 `U-----`，也就是**這些單位的符號外觀完全不變**。
-- 想定不指定兵科時的行為因此與過去逐字相同。
ALTER TABLE `TacticalUnit`
  ADD COLUMN `branch` ENUM(
    'UNKNOWN','INFANTRY','ARMOR','RECON','ARTILLERY','AIR_DEFENSE','ENGINEER',
    'MISSILE','AVIATION','SIGNAL','INTEL','SUPPLY','MEDICAL','MAINTENANCE',
    'TRANSPORT','HQ'
  ) NOT NULL DEFAULT 'UNKNOWN';
