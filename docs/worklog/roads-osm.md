---
task: "#83 道路網 ingestion（OSM graphml）— 沿路加速"
status: PARTIAL（道路完成；土地利用分類未做）
started: 2026-07-28T00:10+08:00
updated: 2026-07-28T01:00+08:00
agent: Opus 5
spec: SPEC_FULL §4.3、SPEC_MOVEMENT §2.2/§3
---

# #83 道路網：沿既成道路移動明顯快於越野

## 關鍵決定：零額外相依
`taiwan_drive.graphml`（osmnx 匯出，326MB）本質是 **XML**，故以標準庫
`xml.etree.ElementTree.iterparse` **串流**解析即可 —— **不需 osmnx / networkx / pyosmium**。
這對 air-gapped 部署是重要的：沒有新增任何需要離線 wheel 的相依。

實測：**15 秒**解析完 326MB，產出 **26,937 個有路的 res-8 格**
（tertiary 9431、primary 5417、unclassified 4805、secondary 3710、motorway 1456、residential 1028）。

## 設計：道路是**疊加**，不是取代
林中公路仍分類為 FOREST（供未來遮蔽/掩蔽使用），另帶 `road_class`。
故 proto **新增欄位**而非改寫 terrain_class：`CellInfo.road_class = 9`（契約先行 → `gen_proto.py` 重生）。

**速度模型**：有可用道路的格 → 改用 `road_kmh`（而非 `xc_kmh`）並乘道路等級係數，
且**不再套地形/坡度成本**（路面已鋪整，林中公路不該按森林算）。無路 → 完全維持 #81 越野模型。

## 交付
- `modules/terrain/terrain/roads.py`（新）：`build_road_index`（串流 graphml → {h3: road_class}，
  邊幾何 LINESTRING + 節點座標，沿線加密取樣 ~100m 避免長路段漏標；一格多路取**最高等級**）、
  `write_road_index` / `read_road_index`（parquet）。
- `contracts/proto/.../terrain.proto` +`CellInfo.road_class`；`contracts/mobility_matrix.json` +`road`
  區塊（`speed_factor_by_class` 15 級 + `usable_by_profile`；BOAT/AIR 不能走公路）。
- `HexGridCache.with_roads()` 疊加道路（`_decorate`，`replace()` 不動原 cell）；
  `TerrainPlugin` 啟動時載 `roads_res{N}.parquet`（缺檔＝無道路資料，移動退回純越野）。
- core：`mobility_matrix.road_speed_factor()`；`terrain_sampler` 以 `"FOREST|primary"` 形式帶回
  （維持既有 tuple 介面不變）；執行器有路時改用 `_road_step_km` 且跳過地形成本。

## 驗收
- 新測試 3：`road_speed_factor` 查表（含 BOAT 不可用路、未知等級）、**公路顯著快於越野**、
  **林中公路不按森林算**（>3×）。terrain 既有 93 測試不受影響。
- gates：**pytest 1056 passed / 8 skipped**、**golden 6 未破**、ruff/mypy(200)/schema-sync/buf 全綠。
- **實測（真資料，容器）**：terrain 啟動載入 26,937 格；
  | 位置 | terrain_class | road_class |
  |---|---|---|
  | 24.15/120.65（國道沿線） | GRASSLAND | secondary（係數 0.85） |
  | 25.04/121.51（台北市區） | WETLAND | primary（係數 0.95） |
  | 23.48/121.05（中央山脈） | MOUNTAIN | 無路 |

## 未完成：土地利用分類（#83 的另一半）
`terrain_class` **仍由坡度+高程推導**，不是真實土地利用——上表台北市區被分類為 `WETLAND`
（因低海拔平坦）即為明證，應為 `URBAN`。要修正需解析 `taiwan.osm.pbf` 的 landuse/natural 圖層，
而 PBF 是二進位 protobuf，**需要 pyosmium（或等價套件）**——這正是本卡唯一會動到 air-gapped
相依的部分，故獨立成卡（#89）由使用者決定是否引入。

替代路徑（免新相依）：`/Volumes/M200/Maps/tiles/taiwan.mbtiles`（planetiler 產出）內含 landuse
向量圖層，可用 sqlite + MVT 解碼取出；但 MVT 解碼同樣需要 protobuf schema 處理，工作量與
pyosmium 相當，故一併留待 #89 評估。

## 產物
道路索引已建於 `/Volumes/M200/Maps/hexcache/roads_res8.parquet`（容器掛載 `/data/hexcache`）。
重建指令見 `roads.py` docstring；資料更新後重跑即可。
