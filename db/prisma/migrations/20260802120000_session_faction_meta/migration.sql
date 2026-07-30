-- AlterTable
-- 本局的陣營顯示資訊（顏色與顯示名），開局從想定快照。
-- 想定 schema 的 `factions[].color` / `display_name` 一直解析得出來、dump 也匯得出去，
-- 但**沒有持久化、沒有任何 API 回傳**：前端 `MapCanvas` 的 palette 參數唯一的呼叫端
-- 傳的是字面量 `{}`，於是所有陣營落回寫死的 BLUE/RED 或 id 雜湊色。
-- 這是 TASKS O6.10 / O10.4 的驗收條文「faction 顏色（scenario 定義）」。
--
-- NULL＝未宣告＝沿用前端預設調色盤（既有局零影響）。
ALTER TABLE `WargameSession`
  ADD COLUMN `factionColors` JSON NULL,
  ADD COLUMN `factionDisplayNames` JSON NULL;
