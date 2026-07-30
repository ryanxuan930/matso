-- AlterTable
-- WP-C4a 晝夜宣告。NULLABLE 無 default：NULL＝未宣告＝整場白天（所有光照係數 1.0），
-- 既有進行中的推演局行為一個位元都不變。
ALTER TABLE `WargameSession` ADD COLUMN `dayNight` JSON NULL;
