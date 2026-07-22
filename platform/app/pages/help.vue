<script setup lang="ts">
// 操作教學（in-app）。不需登入即可閱讀（auth.global 白名單）。
definePageMeta({ public: true })
</script>

<template>
  <main class="help">
    <header>
      <h1>MATSO 操作教學</h1>
      <a class="back" href="/lobby">← 返回大廳</a>
    </header>

    <section>
      <h2>0 · 名詞速覽</h2>
      <ul>
        <li><b>推演 / Session</b>：一場兵棋演習。</li>
        <li><b>陣營 / Faction</b>：交戰方（如 BLUE、RED、YELLOW）。關係為敵對/中立/同盟。</li>
        <li><b>圖台 / COP</b>：共同作戰圖像——地圖 + 我方單位 + 敵情。</li>
        <li><b>白軍 / White Cell</b>：統裁方（EXERCISE_DIRECTOR / WHITE_CELL_STAFF），控制時間與注入事件、全知視角。</li>
        <li><b>Fog of War</b>：戰場迷霧——各陣營只看得到自己單位 + 自己偵測到的敵情。</li>
      </ul>
    </section>

    <section>
      <h2>1 · 登入</h2>
      <ol>
        <li>開 <code>http://localhost:3000</code>，輸入帳號密碼 → <b>登入</b>。</li>
        <li>開發測試帳號：<code>commander / exercise</code>（角色 EXERCISE_DIRECTOR，可見全部推演）。</li>
        <li>要新增帳號：<code>uv run python ops/tools/seed_dev_user.py &lt;帳號&gt; &lt;密碼&gt; &lt;角色&gt;</code>。</li>
      </ol>
    </section>

    <section>
      <h2>2 · 大廳（Lobby）</h2>
      <ul>
        <li>列出你有權限的推演；點任一張卡片進入其<b>圖台</b>。</li>
        <li>右上角 <b>建立推演</b>：輸入名稱即可開新局。</li>
        <li>卡片右側標籤是你在該局的<b>陣營</b>（如 BLUE）。</li>
      </ul>
    </section>

    <section>
      <h2>3 · 圖台（COP）— 主畫面</h2>
      <p>左欄由上而下：</p>
      <ul>
        <li><b>單位</b>：你可下令的我方單位（點選以下令）。敵方單位受迷霧過濾，僅在偵測到時以敵情符號出現在地圖上。</li>
        <li><b>戰況事件 · live</b>：即時裁決事件流（WebSocket）。</li>
        <li><b>指令（pending / 歷史）</b>：已送出的指令與狀態，可取消未執行者。</li>
      </ul>
      <p>右上 <b>圖層</b>：切換六角網格、地形陰影。右上導覽列：<b>白軍控制台</b>（限統裁）、<b>AAR</b>、<b>操作教學</b>。</p>
    </section>

    <section>
      <h2>4 · 下令（Orders）</h2>
      <ol>
        <li>在左欄<b>單位</b>清單點一個我方單位 → 出現「下令」面板。</li>
        <li>選指令類型：
          <ul>
            <li><b>移動 MOVE</b>：按「設定目標點」→ 在地圖上點一格（H3）作為目的地。</li>
            <li><b>攻擊 ENGAGE</b>：從下拉選單選目標單位（僅敵對關係可通過 ROE 預檢）。</li>
          </ul>
        </li>
        <li>按 <b>送出</b>。系統回傳<b>物理預檢</b>（可達性 / ETA / 彈藥 / ROE）——不可行會逐條列出原因。</li>
      </ol>
    </section>

    <section>
      <h2>5 · 白軍控制台（統裁）</h2>
      <p>圖台右上 <b>⚙ 白軍控制台</b> 進入（限 EXERCISE_DIRECTOR / WHITE_CELL_STAFF）。功能：</p>
      <ul>
        <li><b>視角</b>：切換全知（God View）或任一陣營視角——用於統裁與教學。</li>
        <li><b>時間控制</b>：暫停 / 續行 / 回滾到指定 tick。</li>
        <li><b>注入事件</b>：投入劇本事件（如橋樑炸毀），驅動 MSEL。</li>
        <li><b>戰況事件流</b>：完整（未過濾）事件記錄。</li>
      </ul>
    </section>

    <section>
      <h2>6 · AAR（行動後檢討）</h2>
      <p>推演結束後，圖台右上 <b>📊 AAR</b>：時間軸重播、統計儀表板、AI 敘事報告（逐段引用事件 id）、匯出。</p>
    </section>

    <section>
      <h2>7 · 關於地圖底圖</h2>
      <p>目前為<b>離線底圖模式</b>：畫面是經緯格線 + 單位符號，<b>沒有</b>街道/地形影像。這是設計內行為——
        底圖需要本地向量瓦片（air-gapped，不連外部地圖服務）。</p>
      <p>要載入真實台灣底圖：由外接硬碟的 <code>taiwan.osm.pbf</code> 產生 <code>.mbtiles</code>，啟用 compose 的
        <code>tiles</code> profile，並設定 <code>TILE_URL</code>。地形陰影同樣需要 tileserver 提供 hillshade 瓦片。</p>
    </section>

    <section>
      <h2>8 · AI 模式（傳統 / 降級 / 完整）</h2>
      <ul>
        <li><b>AI_OFF</b>（預設）：傳統兵推，全物理引擎，紅藍皆由人操作——目前系統即以此模式運作。</li>
        <li><b>AI_BARE</b>：AI 以自身知識推理（無語料庫），輸出經護欄查核；適合語料為空時測試。</li>
        <li><b>AI_FULL</b>：AI + RAG 引用查核。</li>
      </ul>
      <p>模式為每局設定；AI 永不裁決物理事實（命中/可見/可達由確定性引擎決定），且輸出一律經護欄。</p>
    </section>
  </main>
</template>

<style scoped>
.help {
  max-width: 52rem;
  margin: 0 auto;
  padding: 2rem 1.5rem 4rem;
  color: #e2e8f0;
  line-height: 1.7;
}
header {
  display: flex;
  align-items: baseline;
  justify-content: space-between;
  margin-bottom: 1.5rem;
  border-bottom: 1px solid #1e293b;
  padding-bottom: 0.75rem;
}
h1 { font-size: 1.5rem; margin: 0; }
.back { color: #60a5fa; text-decoration: none; font-size: 0.875rem; }
.back:hover { text-decoration: underline; }
section { margin: 1.5rem 0; }
h2 {
  font-size: 1.0625rem;
  color: #93c5fd;
  margin: 0 0 0.5rem;
}
ul, ol { margin: 0.25rem 0; padding-left: 1.5rem; }
li { margin: 0.25rem 0; }
b { color: #f1f5f9; }
code {
  background: #0f172a;
  border: 1px solid #1e293b;
  border-radius: 0.25rem;
  padding: 0.0625rem 0.375rem;
  font-size: 0.85em;
  color: #7dd3fc;
}
p { margin: 0.5rem 0; }
</style>
