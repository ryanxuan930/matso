---
task: "#89 土地利用分類（OSM PBF landuse）"
status: DONE
started: 2026-07-28T01:10+08:00
updated: 2026-07-28T02:00+08:00
agent: Opus 5
spec: SPEC_FULL §4.2（terrain_class）
---

# #89 土地利用：terrain_class 不再靠坡度猜

## 問題（#83 留下的另一半）
`hexgrid.classify_terrain` 只用坡度+高程，**產不出 URBAN/FOREST**（原始碼自己註明「需土地利用
資料，Phase 2」）。實測證據：**台北車站被判 `WETLAND`**（低海拔又平坦），應為 URBAN。

## 相依決定（本卡唯一動到相依處）
`taiwan.osm.pbf` 是二進位 protobuf，必須用 `osmium`（pyosmium）。已確認 **4.3.1 有預編譯 wheel、
安裝乾淨**，加入 `modules/terrain` 相依。**僅離線預計算需要**——terrain 服務執行期只讀 parquet，
不 import osmium（air-gapped 執行期不受影響）。

## 做法
- `modules/terrain/terrain/landuse.py`（新）：`osmium.FileProcessor(...).with_areas()` 串流 PBF，
  依 landuse/natural tag 對照 `TerrainClass`（URBAN/FOREST/WETLAND/WATER/GRASSLAND/BARREN）。
- **兩種取樣**：面狀土地利用 → `h3.h3shape_to_cells` 填滿多邊形（小於一格則取中心格）；
  **建物 → centroid 計數**（單棟遠小於 res-8 格填不出來），一格 ≥12 棟即判 URBAN（市區密度代理）。
- 同格多類 → 優先序 WATER > URBAN > WETLAND > FOREST > BARREN > GRASSLAND
  （WATER 最高：誤判水域為陸地會讓單位「開進湖裡」，寧可保守）。
- 疊加規則（`HexGridCache._decorate`）：**DEM 的 WATER（海面 nodata）與 MOUNTAIN（陡峭）優先於
  土地利用**——前者關乎可通行性正確、後者關乎機動難度（森林覆蓋的陡山對機動而言仍是山地）；
  其餘以真實土地利用為準。未注入 → 完全維持既有行為。

## 實測（真資料）
建索引：**18 秒**解析 308MB PBF → 171,192 個面 → **52,570 格**
（FOREST 32,668、WATER 9,768、URBAN 6,803〔其中 6,103 由建物密度判定〕、GRASSLAND 2,624、
BARREN 406、WETLAND 301）。

服務載入後（容器實測）：
| 位置 | terrain_class | road_class | 備註 |
|---|---|---|---|
| 台北車站 | **URBAN** | trunk | **原為 WETLAND，本卡修正** |
| 高雄市區 | **URBAN** | secondary | |
| 溪頭 | MOUNTAIN | secondary | DEM 高程 >1000m → 山地優先（設計如此） |
| 中央山脈 | MOUNTAIN | — | |
| 日月潭 | WATER | primary | 環潭公路 |

## 驗收
- 新測試 3：土地利用覆蓋坡度猜測（WETLAND→URBAN）、DEM WATER/MOUNTAIN 優先、未注入不動。
- gates：**pytest 1059 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(201) 全綠；terrain 重建實測。

## 已知近似（記錄，非 bug）
- **水域格上有路仍可通行**：日月潭格為 WATER 但有環潭公路，執行器道路優先 → 可通過。
  現實中確有橋樑/環湖道路，且 res-8（0.74km²）granularity 下這是合理近似；若日後需嚴格化，
  可要求道路與水域共存時降速而非全速。
- 森林覆蓋的陡峭山地歸 MOUNTAIN 而非 FOREST（機動難度優先）——刻意設計，見上。
- 建物密度門檻 12 棟/格為經驗值，可調。

## 產物
`/Volumes/M200/Maps/hexcache/landuse_res8.parquet`（容器掛載 `/data/hexcache`）。
資料更新後重跑 `build_landuse_index` 即可；重建指令見模組 docstring。
