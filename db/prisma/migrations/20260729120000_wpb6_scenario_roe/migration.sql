-- WP-B6 想定 ROE 持久化：WargameSession 新增 roe（交戰規則宣告 JSON 物件）。
-- 可為 NULL：既有推演局維持 NULL ＝ 無 ROE 限制 ＝ 裁決層與 precheck 皆不篩（過去語義，零資料遷移）。
-- 存宣告（default_fire_policy / weapon_restrictions）而非展開後的武器 id 集合：
-- 武器實例隨編裝變動，展開會立刻過期；且白軍可局中修改 ROE（SPEC_FULL §12 主席權限），
-- 讀取端每次現讀才能讓變更生效（同 noStrikeZones 的紀律）。
-- AlterTable
ALTER TABLE `WargameSession` ADD COLUMN `roe` JSON NULL;
