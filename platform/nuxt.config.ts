// https://nuxt.com/docs/api/configuration/nuxt-config
import tailwindcss from "@tailwindcss/vite";
import Aura from '@primeuix/themes/aura';

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  modules: ['@nuxt/fonts', '@nuxt/scripts', '@nuxt/eslint', '@pinia/nuxt', '@primevue/nuxt-module'],
  // PrimeVue（v5 + @primeuix/themes Aura 預設）；darkModeSelector 對齊 app.vue 的全域深色基底。
  // cssLayer 關閉（未用 @layer；避免與 Tailwind v4 的層級順序衝突）。
  primevue: {
    options: {
      theme: {
        preset: Aura,
        options: { darkModeSelector: '.app-dark', cssLayer: false },
      },
    },
  },
  // MapLibre GL 樣式表本地打包（不引外部 CDN，air-gapped）
  css: ['maplibre-gl/dist/maplibre-gl.css'],
  // 元件依區域分目錄（components/<區域>/）但以檔名自動匯入（不加路徑前綴），HOW_TO §3.2
  components: [{ path: '~/components', pathPrefix: false }],
  runtimeConfig: {
    public: {
      // Core REST 位址（endpoints 在 /api/v1）。compose 由 NUXT_PUBLIC_API_BASE 注入。
      apiBase: 'http://localhost:8000',
      // 離線 tile server（O4.2）。空字串＝無底圖（地圖仍以背景+經緯網格+hex 離線渲染）。
      // compose 由 NUXT_PUBLIC_TILE_URL 注入（tileserver-gl，掛載 M200 的 .mbtiles 時）。
      tileUrl: '',
      // 衛星影像 raster XYZ 模板（#2；NUXT_PUBLIC_SATELLITE_URL）——未來接軍用/商用影像的抽換點。
      satelliteUrl: '',
      // 額外自訂底圖來源（#2；NUXT_PUBLIC_BASEMAPS，JSON 陣列 of BasemapSource）——軍方資料接入點。
      basemaps: [] as unknown[],
      // 啟用 Google/Esri 線上底圖（NUXT_PUBLIC_ONLINE_BASEMAPS）。需外網，非 air-gapped，預設關。
      onlineBasemaps: false,
    },
  },
  vite: {
    plugins: [
      tailwindcss(),
    ],
  },
})
