# GRASS r.viewshed 對照驗證（O2.3 驗收，release 前必跑）

驗收目標（TASKS O2.3）：我方 `get_viewshed` 與 GRASS GIS `r.viewshed` 在 **100 個抽樣觀測點**
的可見性一致率 **≥ 98%**。這是外部權威實作的交叉驗證，**CI 可 skip、release 前必跑**。

## 為何 skip on CI
- 需要 GRASS GIS（以 docker `osgeo/grass-gis` 執行）與**真檔 DTED**（外接硬碟）。
- 兩者在一般 CI 環境不存在——`test_grass_compare.py` 偵測不到即自動 skip（marker: `grass`）。

## 執行方式（本機，硬碟掛載 + docker 就緒）
```bash
export MATSO_DTED_PATH=/Volumes/M200/Maps/TW_ALL.tif
uv run pytest modules/terrain/tests/grass_compare -m grass -v --no-cov
# 或獨立跑比對腳本（印出一致率）
uv run python modules/terrain/tests/grass_compare/compare.py
```

## 方法（compare.py）
1. 在 DTED 覆蓋範圍內以固定 seed 取 100 個觀測點（確定性，非亂數）。
2. 每點：
   - 我方：`get_viewshed(observer, radius)` → 可見 h3 cell 集合 → 取各 cell 中心點視為「可見樣本」。
   - GRASS：把 DTED 匯入 GRASS location，對同一觀測點跑 `r.viewshed`（同 observer AGL、
     `max_distance=radius`、`-c` 曲率修正）→ 輸出可見性 raster；於相同 cell 中心取樣。
3. 逐點比對「可見/不可見」布林，統計整體一致率；assert ≥ 0.98。

## 目前狀態
- 骨架 + 確定性抽樣 + 我方 viewshed 呼叫已完成。
- **待完成（release 前）**：GRASS docker 呼叫與 raster↔點取樣（`_grass_visibility`）——
  見 compare.py 的 NotImplementedError 與 PROGRESS backlog。
