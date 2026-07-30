-- WP-A2：子令回指母任務令。
-- NULL ＝ 直接下的令（既有令零遷移，本 repo 每次加欄的 house invariant）。
-- 無 FK：母令被硬刪時子令不該連坐消失——那些子令是既成事實，AAR 要看得到。

ALTER TABLE `Order` ADD COLUMN `parentOrderId` VARCHAR(191) NULL;
CREATE INDEX `Order_parentOrderId_idx` ON `Order`(`parentOrderId`);
