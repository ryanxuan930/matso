-- AlterTable
-- WP-C9 友軍誤傷裁決開關。NULLABLE 且無 default：NULL＝未宣告＝維持既有
-- 「非敵對一律拒」的語義，既有進行中的推演局行為一個位元都不變。
ALTER TABLE `WargameSession` ADD COLUMN `allowFratricide` BOOLEAN NULL;
