#!/usr/bin/env bash
# 由 DTED（TW_ALL.tif）產生 hillshade（地形陰影）+ contour（等高線）mbtiles（#3）。
#
# air-gapped：用 osgeo/gdal docker 一次性離線產生（repo runtime 不依賴 GDAL CLI；rasterio 綁的
# libgdal 無 CLI apps，且 terrain 容器唯讀掛載 /data 無法寫出）。tileserver-gl-light 依 basename
# 自動服務：hillshade.mbtiles → /data/hillshade/{z}/{x}/{y}.png、contours.mbtiles → /data/contours/{z}/{x}/{y}.pbf。
#
# 用法：MATSO_DTED_PATH=/Volumes/M200/Maps/TW_ALL.tif MBTILES_DIR=/Volumes/M200/Maps/tiles \
#         ops/tools/build_terrain_tiles.sh
# 產完重啟 tileserver：cd ops/compose && docker compose --profile tiles restart tileserver
set -euo pipefail

GDAL_IMAGE="${GDAL_IMAGE:-ghcr.io/osgeo/gdal:ubuntu-small-latest}"
DTED="${MATSO_DTED_PATH:-/Volumes/M200/Maps/TW_ALL.tif}"
OUT="${MBTILES_DIR:-/Volumes/M200/Maps/tiles}"
# 等高線「基底」間距（公尺）。前端主/次等高線（#8）以 elev%interval 篩選，故基底須能整除
# 想要的主/次值。預設 50 → 支援主 100/次 50（皆為 50 倍數），tile 約為 100m 版的 2 倍。
CONTOUR_INTERVAL="${CONTOUR_INTERVAL:-50}"
# CONTOUR_ONLY=1 時只重建 contours.mbtiles（跳過 hillshade——已存在且不需隨等高線間距改變）。
CONTOUR_ONLY="${CONTOUR_ONLY:-0}"
SRC_DIR="$(cd "$(dirname "$DTED")" && pwd)"
SRC_FILE="$(basename "$DTED")"

[ -f "$DTED" ] || { echo "DTED 不存在：$DTED（掛上外接硬碟或設 MATSO_DTED_PATH）"; exit 1; }
mkdir -p "$OUT"
echo "GDAL=$GDAL_IMAGE  DTED=$DTED  OUT=$OUT  等高線間距=${CONTOUR_INTERVAL}m"

docker run --rm -e CONTOUR_ONLY="$CONTOUR_ONLY" -e CONTOUR_INTERVAL="$CONTOUR_INTERVAL" \
  -v "$SRC_DIR":/src:ro -v "$OUT":/out "$GDAL_IMAGE" bash -c "
set -euo pipefail
cd /tmp
if [ \"\$CONTOUR_ONLY\" != 1 ]; then
  echo '[1/4] warp DTED → EPSG:3857（讓 hillshade 的 z-factor 在公尺尺度正確）'
  gdalwarp -t_srs EPSG:3857 -r bilinear -overwrite /src/$SRC_FILE dem3857.tif
  echo '[2/4] gdaldem hillshade（多向光照，適合台灣陡地形）'
  gdaldem hillshade -multidirectional dem3857.tif hillshade.tif
  echo '[3/4] hillshade → raster mbtiles + overviews'
  rm -f /out/hillshade.mbtiles
  gdal_translate -of MBTILES hillshade.tif /out/hillshade.mbtiles
  gdaladdo -r average /out/hillshade.mbtiles 2 4 8 16 32
else
  echo '[CONTOUR_ONLY] 跳過 hillshade（沿用既有 hillshade.mbtiles）'
fi
echo \"[4/4] gdal_contour（基底間距 \${CONTOUR_INTERVAL}m）→ vector mbtiles（source-layer=contour）\"
gdal_contour -a elev -i \$CONTOUR_INTERVAL /src/$SRC_FILE contours.gpkg -nln contour
rm -f /out/contours.mbtiles
ogr2ogr -f MBTILES -t_srs EPSG:3857 -dsco MAXZOOM=14 -nln contour /out/contours.mbtiles contours.gpkg
echo 'done'
"
# tileserver-gl-light 的 auto-mode 只服務單一 mbtiles；多來源需 config.json。
# 對映到前端預期路徑：街道=/data/v3、地形陰影=/data/hillshade、等高線=/data/contours。
cat > "$OUT/config.json" <<'JSON'
{
  "options": { "paths": { "root": "/data", "mbtiles": "/data" } },
  "data": {
    "v3": { "mbtiles": "taiwan.mbtiles" },
    "hillshade": { "mbtiles": "hillshade.mbtiles" },
    "contours": { "mbtiles": "contours.mbtiles" }
  }
}
JSON
echo "完成 → $OUT（含 config.json）。重啟 tileserver 生效：cd ops/compose && docker compose --profile tiles restart tileserver"
