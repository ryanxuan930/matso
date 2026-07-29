-- WP-A3 禁射區持久化：WargameSession 新增 noStrikeZones（宣告式區域 JSON 陣列）。
-- 可為 NULL：既有推演局維持 NULL ＝ 無禁射區 ＝ G4 與 precheck 皆不攔（過去語義，零資料遷移）。
-- 存宣告（name/zone_class/geometry）而非 h3 格集：格集由 geometry 於讀取時導出（白軍可局中增修，
-- 且 res-8 下一個中型區域即數百格，存格集會讓欄位膨脹且與宣告重複）。
-- AlterTable
ALTER TABLE `WargameSession` ADD COLUMN `noStrikeZones` JSON NULL;
