// Nuxt 產生的 flat config（.nuxt/eslint.config.mjs）為基底；專案規則加在 append 區。
import withNuxt from './.nuxt/eslint.config.mjs'

export default withNuxt(
  // HOW_TO.md §3.2：元件一律 <script setup lang="ts">；API 型別由 contracts 生成，禁止手寫
  {
    rules: {
      'vue/multi-word-component-names': 'off',
    },
  },
)
