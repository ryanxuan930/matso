-- AlterTable
ALTER TABLE `TacticalUnit` ADD COLUMN `authorizedStrength` DOUBLE NOT NULL DEFAULT 100.0,
    ADD COLUMN `currentStrength` DOUBLE NOT NULL DEFAULT 100.0,
    ADD COLUMN `personnelAuthorized` INTEGER NULL,
    ADD COLUMN `personnelCurrent` INTEGER NULL;
