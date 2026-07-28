// https://nuxt.com/docs/api/configuration/nuxt-config
import tailwindcss from "@tailwindcss/vite";
import { definePreset } from '@primevue/themes';
import Aura from '@primevue/themes/aura';

// 主色調＝Blue（對齊深色背景）。以 Aura primitive 的 blue 色階覆蓋 semantic primary。
const MatsoAura = definePreset(Aura, {
  semantic: {
    primary: {
      50: '{blue.50}',
      100: '{blue.100}',
      200: '{blue.200}',
      300: '{blue.300}',
      400: '{blue.400}',
      500: '{blue.500}',
      600: '{blue.600}',
      700: '{blue.700}',
      800: '{blue.800}',
      900: '{blue.900}',
      950: '{blue.950}',
    },
  },
});

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  // 全站套用 Aura Dark（html.app-dark 對齊 darkModeSelector），讓 PrimeVue 元件與深色背景一致。
  app: { head: { htmlAttrs: { class: 'app-dark' } } },
  modules: ['@nuxt/fonts', '@nuxt/scripts', '@nuxt/eslint', '@pinia/nuxt', '@primevue/nuxt-module'],
  // PrimeVue（v4 + Aura Dark，主色 Blue；v4 為 Apache-2.0 無授權水印，air-gapped 友善）。
  // darkModeSelector='.app-dark'（永遠掛在 html 上 → 恆深色）；cssLayer 關閉（未用 @layer）。
  primevue: {
    options: {
      theme: {
        preset: MatsoAura,
        options: { darkModeSelector: '.app-dark', cssLayer: false },
      },
    },
  },
  // MapLibre GL 樣式表 + PrimeIcons 圖標字型（皆本地打包，不引外部 CDN，air-gapped）。
  css: ['maplibre-gl/dist/maplibre-gl.css', 'primeicons/primeicons.css'],
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
