<script setup lang="ts">
/**
 * 圖層 / 底圖小工具的內容——把 23 條 v-model 從頁面搬進來。
 *
 * 這些偏好全部同源於 `useCopPrefs()`（同一把 localStorage 鑰匙持久化），所以整包以
 * `prefs` prop 收下（父層須傳 `reactive(...)`，樣板不會 unwrap 巢狀 ref），
 * 而不是拆成 23 個 prop + 23 個 emit 再由頁面轉手。
 *
 * 本元件只做轉接：真正的圖層 UI 在 `LayerToggles`，這裡不加任何自己的狀態或樣式。
 */
import type { UnwrapNestedRefs } from 'vue'
import type { useCopPrefs } from '~/composables/useCopPrefs'

defineProps<{ prefs: UnwrapNestedRefs<ReturnType<typeof useCopPrefs>> }>()
</script>

<template>
<!-- eslint-disable vue/no-mutating-props -- prefs 是 useCopPrefs() 整包偏好（父層 reactive），
     v-model 寫回的就是它持有的那份 ref，與持久化 watcher 同源；另存區域副本反而會失去存檔。 -->
<LayerToggles
  v-model:hex="prefs.hex"
  v-model:hillshade="prefs.hillshade"
  v-model:contour="prefs.contour"
  v-model:basemap="prefs.basemap"
  v-model:layer-opacity="prefs.layerOpacity"
  v-model:layer-order="prefs.layerOrder"
  v-model:contour-major="prefs.contourMajor"
  v-model:contour-minor="prefs.contourMinor"
  v-model:latlng-grid="prefs.latlngGrid"
  v-model:mgrs-grid="prefs.mgrsGrid"
  v-model:grid-step-deg="prefs.gridStepDeg"
  v-model:hex-max-res="prefs.hexMaxRes"
  v-model:hex-limit-km="prefs.hexLimitKm"
  v-model:day-night="prefs.dayNight"
  v-model:time-of-day="prefs.timeOfDay"
  v-model:hex-line-width="prefs.hexLineWidth"
  v-model:contour-major-width="prefs.contourMajorWidth"
  v-model:contour-minor-width="prefs.contourMinorWidth"
  v-model:hex-line-color="prefs.hexLineColor"
  v-model:contour-color="prefs.contourColor"
  v-model:grid-color="prefs.gridColor"
  v-model:grid-width="prefs.gridWidth"
  v-model:mgrs-color="prefs.mgrsColor"
  :hillshade-enabled="prefs.hasTiles"
  :contour-enabled="prefs.hasTiles"
  :basemaps="prefs.basemapSources"
/>
</template>
