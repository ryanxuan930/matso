-- AlterTable
-- WP-E2 帳號鎖定。NULLABLE 無 default：NULL＝從未失敗過（既有列語義不變）。
ALTER TABLE `User` ADD COLUMN `failedAttempts` INTEGER NULL;
ALTER TABLE `User` ADD COLUMN `lockedUntil` DATETIME(3) NULL;

-- CreateTable
-- WP-E2 refresh token 撤銷表。以 jti 為鍵；過期的列可安全清除。
CREATE TABLE `RevokedToken` (
    `jti` VARCHAR(191) NOT NULL,
    `userId` VARCHAR(191) NOT NULL,
    `expiresAt` DATETIME(3) NOT NULL,
    `reason` VARCHAR(191) NOT NULL,
    `revokedAt` DATETIME(3) NOT NULL DEFAULT CURRENT_TIMESTAMP(3),

    INDEX `RevokedToken_userId_idx`(`userId`),
    INDEX `RevokedToken_expiresAt_idx`(`expiresAt`),
    PRIMARY KEY (`jti`)
) DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
