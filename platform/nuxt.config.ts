// https://nuxt.com/docs/api/configuration/nuxt-config
import tailwindcss from "@tailwindcss/vite";

export default defineNuxtConfig({
  compatibilityDate: '2025-07-15',
  devtools: { enabled: true },
  modules: ['@nuxt/fonts', '@nuxt/scripts', '@nuxt/eslint', '@pinia/nuxt'],
  runtimeConfig: {
    public: {
      // Core REST 位址（endpoints 在 /api/v1）。compose 由 NUXT_PUBLIC_API_BASE 注入。
      apiBase: 'http://localhost:8000',
    },
  },
  vite: {
    plugins: [
      tailwindcss(),
    ],
  },
})
