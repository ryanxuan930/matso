<script setup lang="ts">
/**
 * C2 信文與申請-核覆小工具（WP-B5.2）。
 *
 * 兩個分頁：**信文匣**（收發）與**申請單**（送出/核覆 + 配額用量）。
 *
 * 可見性、核覆權、配額**全部由後端決定**——這裡只呈現後端回什麼。
 * 尤其收信匣：不在前端做任何過濾（紅線 3），後端已用與 WS 同一套受眾規則濾過。
 * 核覆按鈕的顯示只是 UX，越權時後端仍會回 REQUEST_APPROVAL_DENIED。
 */
import { computed, onMounted, ref, watch } from 'vue'
import {
  KINDS_NEEDING_TARGET,
  MESSAGE_KIND_LABELS,
  REQUEST_KIND_LABELS,
  REQUEST_STATUS_LABELS,
  decideRequest,
  fetchMessages,
  fetchRequests,
  markMessagesRead,
  sendMessage,
  submitRequest,
  type MessageView,
  type RequestKind,
  type RequestList,
} from '~/composables/useC2'
import { SEAT_ROLE_LABELS, fetchRoster } from '~/composables/useParticipants'

const props = defineProps<{
  sessionId: string
  /** 本人席位；null＝未指派（權限沿用角色規則）。 */
  mySeat: string | null
  /**
   * COP 下令面板當前的火力落點。臨機火力申請要帶目標座標——
   * **與火力任務共用同一套點地圖互動**：兩處各做一套，使用者要學兩次，
   * 而且第二套一定會漏掉某個提醒。
   */
  aimPoint?: { lng: number; lat: number } | null
}>()

const auth = useAuthStore()
const stream = useSessionStreamStore()

const tab = ref<'inbox' | 'requests'>('inbox')
const messages = ref<MessageView[]>([])
const reqs = ref<RequestList | null>(null)
const busy = ref(false)
const err = ref('')
const draft = ref('')
const draftSeat = ref('')
const draftFaction = ref('')
const factionOptions = ref<string[]>([])
const reqKind = ref<RequestKind>('AIR_RECON')
const reqNote = ref('')

/**
 * 跨陣營發信的入口只給白軍/統裁看——**這只是 UX**，真正的閘門在後端
 * （`_resolve_to_faction`：非白軍指定他人陣營一律 403）。前端不做權限，也不做迷霧過濾。
 */
const canCrossFaction = computed(() =>
  ['EXERCISE_DIRECTOR', 'WHITE_CELL_STAFF'].includes(auth.user?.role ?? ''),
)

/** 席位全名太長（含權限括號），信文標頭只取前段。 */
function shortSeat(seat: string): string {
  return (SEAT_ROLE_LABELS[seat] ?? seat).replace(/（.*$/, '')
}
function factionLabel(f: string): string {
  return f === 'WHITE_CELL' ? '統裁' : f
}
/**
 * 收件對象。原本一律顯示「→ 全軍」，那是錯的：未指定席位＝**該陣營全體**，不是全軍；
 * 白軍跨陣營發信之後更是分不出這封是發給誰的。
 */
function addressee(m: MessageView): string {
  const f = m.to_faction ? factionLabel(m.to_faction) : ''
  const seat = m.to_seat ? shortSeat(m.to_seat) : ''
  if (!f) return seat || '全體'
  return seat ? `${f} · ${seat}` : `${f} 全體`
}
/** 寄件備份（自己寄的）——已讀狀態對它的意思是「對方讀了沒」，且不該由自己標示。 */
function isMine(m: MessageView): boolean {
  return !!auth.user && m.from_username === auth.user.username
}
function readLabel(m: MessageView): string {
  if (isMine(m)) return m.read_at ? '對方已讀' : '對方未讀'
  return m.read_at ? '已讀' : '未讀'
}

/** 可由本人標示已讀的信文（不是自己寄的、且還沒被標過）。 */
const unread = computed(() => messages.value.filter((m) => !m.read_at && !isMine(m)))

async function reload() {
  err.value = ''
  try {
    ;[messages.value, reqs.value] = await Promise.all([
      fetchMessages(props.sessionId),
      fetchRequests(props.sessionId),
    ])
  } catch (e) {
    err.value = `載入失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  }
}

/** 可選的收件陣營（本局實際存在的）。名冊是統裁級端點，非白軍拿不到也不影響信文匣。 */
async function loadFactions() {
  if (!canCrossFaction.value) return
  try {
    factionOptions.value = (await fetchRoster(props.sessionId)).factions
  } catch {
    factionOptions.value = []
  }
}

/**
 * ⚠ **不可以放在 `onMounted` 裡**。
 *
 * `canCrossFaction` 讀 `auth.user?.role`，而 `auth.user` 是父層 `cop.vue` 的 `onMounted`
 * 裡 `await auth.fetchMe()` 才填的——子元件的 `onMounted` 早於父元件，而且那是一次真的
 * HTTP 往返。於是 `loadFactions()` 在 `auth.user` 還是 null 時就 `return` 掉，
 * 收件陣營選單永遠是空的。
 *
 * 而且這個路徑**是最常見的那一條**：信文視窗的開關狀態存在 localStorage，
 * 統裁上次留著開，這次一進來面板就掛載。首次造訪（視窗預設關、手動點開）反而正常
 * ——「有時會動、重整就不會動」是最難查的那種病。
 *
 * 改成監看 `canCrossFaction`：值到齊的那一刻才抓。
 */
watch(canCrossFaction, (may) => {
  if (may) void loadFactions()
}, { immediate: true })

onMounted(() => {
  void reload()
  // `loadFactions` 不在這裡——它依賴 `auth.user`，而那要等父層的 fetchMe 回來。
  // 見下方 `watch(canCrossFaction, …)` 的說明。
})

/**
 * 後端送信/核覆時一直有推 `C2_MESSAGE` / `C2_REQUEST`，**前端從來沒有訂閱端**——
 * 於是信文匣只在掛載時抓一次，新信與已讀狀態非重整頁面看不到。
 * 只認 C2_* 事件：每個 STATE_DIFF 都去打 API 會把後端打爆。
 */
const C2_EVENTS = new Set(['C2_MESSAGE', 'C2_MESSAGE_READ', 'C2_REQUEST'])
watch(
  () => stream.events[stream.events.length - 1],
  (last) => {
    // ⚠ 讀 `payload.event_type`，**不是 `last.type`**。
    // 串流信封的形狀是 `{type: 'EVENT', payload: {event_type: 'C2_MESSAGE', …}}`
    // ——`last.type` 對每一則事件都是字串 `'EVENT'`，拿它比對 C2_* 恆為 false，
    // 這個 watch 會一次都不觸發（而畫面看起來只是「新信沒有自己跳出來」）。
    // 同一個 repo 裡的正確寫法：`cop.vue` 的 SESSION_CONTROL 判讀、`useCopFeed` 的 formatEvent。
    const kind = (last?.payload as { event_type?: string } | undefined)?.event_type
    if (kind && C2_EVENTS.has(kind)) void reload()
  },
)

async function doSend() {
  if (!draft.value.trim()) return
  busy.value = true
  try {
    await sendMessage(props.sessionId, draft.value, {
      toSeat: draftSeat.value || null,
      toFaction: draftFaction.value || null,
    })
    draft.value = ''
    await reload()
  } catch (e) {
    err.value = `送信失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    busy.value = false
  }
}

/** 標示已讀；`ids` 省略＝把所有寄給我的未讀一次標掉。 */
async function doMarkRead(ids?: string[]) {
  busy.value = true
  try {
    const res = await markMessagesRead(props.sessionId, ids)
    await reload()
    // 後端會跳過已讀過/自己寄的/不是寄給我的——沒標到就明講，不要讓按鈕看起來有效。
    if (!res.marked.length) err.value = '沒有可標示的信文（已讀過，或不是寄給你的）'
  } catch (e) {
    err.value = `標示已讀失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    busy.value = false
  }
}

async function doSubmit() {
  busy.value = true
  try {
    const needsTarget = KINDS_NEEDING_TARGET.has(reqKind.value)
    if (needsTarget && !props.aimPoint) {
      err.value = '臨機火力申請要先在「單位／下令」面板點出目標落點'
      return
    }
    await submitRequest(
      props.sessionId,
      reqKind.value,
      reqNote.value,
      needsTarget && props.aimPoint
        ? { target_lat: props.aimPoint.lat, target_lng: props.aimPoint.lng }
        : {},
    )
    reqNote.value = ''
    await reload()
  } catch (e) {
    err.value = `送出申請失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    busy.value = false
  }
}

async function doDecide(rid: string, approve: boolean) {
  busy.value = true
  try {
    await decideRequest(props.sessionId, rid, approve)
    await reload()
  } catch (e) {
    err.value = `核覆失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
  } finally {
    busy.value = false
  }
}

const pending = computed(() => (reqs.value?.requests ?? []).filter((r) => r.status === 'PENDING'))
/** 只是 UX：後端才是核覆權的權威。 */
const mayDecide = computed(() => props.mySeat === 'COMMANDER' || props.mySeat === null)
</script>

<template>
<div class="c2">
  <div class="c2-seat">
    席位：<b>{{ mySeat ? (SEAT_ROLE_LABELS[mySeat] ?? mySeat) : '未指派（沿用角色權限）' }}</b>
  </div>
  <div class="c2-tabs">
    <button :class="{ on: tab === 'inbox' }" data-testid="c2-tab-inbox" @click="tab = 'inbox'">
      信文匣（{{ messages.length }}）
    </button>
    <button :class="{ on: tab === 'requests' }" data-testid="c2-tab-requests" @click="tab = 'requests'">
      申請單<span v-if="pending.length"> · {{ pending.length }} 待核</span>
    </button>
  </div>
  <p v-if="err" class="c2-err" data-testid="c2-error">{{ err }}</p>

  <template v-if="tab === 'inbox'">
    <div class="c2-inbox-hd">
      <span :class="{ dim: !unread.length }" data-testid="c2-unread-count">未讀 {{ unread.length }}</span>
      <button
        v-if="unread.length"
        :disabled="busy"
        data-testid="c2-mark-all-read"
        @click="doMarkRead()"
      >全部標示已讀</button>
    </div>
    <ul class="c2-list" data-testid="c2-messages">
      <li v-for="m in messages" :key="m.id" :class="{ unread: !m.read_at && !isMine(m) }" data-testid="c2-message">
        <div class="m-hd">
          <span class="m-kind">{{ MESSAGE_KIND_LABELS[m.kind] ?? m.kind }}</span>
          <span class="m-from">{{ m.from_username }}</span>
          <!-- 寄件席位：後端一直有給，信文匣卻只顯示帳號——參謀分不出這封是哪個席位發的 -->
          <span v-if="m.from_seat" class="m-seat" data-testid="c2-from-seat">
            〔{{ shortSeat(m.from_seat) }}〕
          </span>
          <span class="m-to" data-testid="c2-msg-to">→ {{ addressee(m) }}</span>
          <span
            class="m-read"
            :class="{ 'is-read': !!m.read_at }"
            :title="m.read_at ? `已讀時戳 ${m.read_at}` : '收件方尚未標示已讀'"
            data-testid="c2-read-state"
          >{{ readLabel(m) }}</span>
          <span class="m-tick">T{{ m.tick }}</span>
        </div>
        <div class="m-body">{{ m.body }}</div>
        <div v-if="!m.read_at && !isMine(m)" class="r-act">
          <button :disabled="busy" data-testid="c2-mark-read" @click="doMarkRead([m.id])">標示已讀</button>
        </div>
      </li>
      <li v-if="!messages.length" class="empty">（無信文）</li>
    </ul>
    <div class="c2-compose">
      <!-- 跨陣營發信（白軍/統裁）。一般席位看不到這個選單，後端仍會擋越權。 -->
      <select
        v-if="canCrossFaction"
        v-model="draftFaction"
        class="c2-sel"
        data-testid="c2-to-faction"
      >
        <option value="">本陣營</option>
        <option v-for="f in factionOptions" :key="f" :value="f">{{ factionLabel(f) }}</option>
      </select>
      <select v-model="draftSeat" class="c2-sel" data-testid="c2-to-seat">
        <option value="">全體</option>
        <option v-for="(label, k) in SEAT_ROLE_LABELS" :key="k" :value="k">{{ label }}</option>
      </select>
      <input v-model="draft" placeholder="信文內容" data-testid="c2-draft" @keyup.enter="doSend">
      <button :disabled="busy || !draft.trim()" data-testid="c2-send" @click="doSend">送出</button>
    </div>
  </template>

  <template v-else>
    <ul class="c2-quota" data-testid="c2-quotas">
      <li v-for="q in reqs?.quotas ?? []" :key="q.kind">
        {{ REQUEST_KIND_LABELS[q.kind] ?? q.kind }}
        <b>{{ q.used }}{{ q.limit == null ? '' : ` / ${q.limit}` }}</b>
        <span v-if="q.limit == null" class="dim">（不限）</span>
      </li>
    </ul>
    <ul class="c2-list" data-testid="c2-requests">
      <li v-for="r in reqs?.requests ?? []" :key="r.id" data-testid="c2-request">
        <div class="m-hd">
          <span class="m-kind">{{ REQUEST_KIND_LABELS[r.kind] ?? r.kind }}</span>
          <span class="r-st" :class="`st-${r.status}`">{{ REQUEST_STATUS_LABELS[r.status] ?? r.status }}</span>
          <span class="m-from">{{ r.requested_by }}</span>
          <span v-if="r.requested_seat" class="m-seat" data-testid="c2-req-seat">
            〔{{ shortSeat(r.requested_seat) }}〕
          </span>
          <span class="m-tick">T{{ r.requested_at_tick }}</span>
        </div>
        <!-- 核覆留痕（誰、第幾 tick）。schema 註解寫明這是給 AAR 重建事件鏈用的，
             但申請人在畫面上一直看不到是誰核的——只看得到一句核覆說明。 -->
        <div v-if="r.decided_by" class="r-decided" data-testid="c2-decided-by">
          核覆：{{ r.decided_by }} · T{{ r.decided_at_tick ?? '?' }}
        </div>
        <div v-if="r.decision_note" class="m-body dim">{{ r.decision_note }}</div>
        <div v-if="r.status === 'PENDING' && mayDecide" class="r-act">
          <button data-testid="c2-approve" :disabled="busy" @click="doDecide(r.id, true)">核准</button>
          <button data-testid="c2-deny" :disabled="busy" @click="doDecide(r.id, false)">駁回</button>
        </div>
      </li>
      <li v-if="!(reqs?.requests ?? []).length" class="empty">（無申請單）</li>
    </ul>
    <div class="c2-compose">
      <select v-model="reqKind" class="c2-sel" data-testid="c2-req-kind">
        <option v-for="(label, k) in REQUEST_KIND_LABELS" :key="k" :value="k">{{ label }}</option>
      </select>
      <p v-if="KINDS_NEEDING_TARGET.has(reqKind)" class="c2-hint" data-testid="c2-cff-target">
        <template v-if="aimPoint">
          🎯 目標 {{ aimPoint.lat.toFixed(4) }}, {{ aimPoint.lng.toFixed(4) }}
        </template>
        <template v-else>
          ⚠ 先在「單位／下令」面板點出目標落點——沒有觀測就叫不動火力，
          後端會驗本陣營是否有單位看得到那個點。
        </template>
      </p>
      <input v-model="reqNote" placeholder="申請說明（選填）" data-testid="c2-req-note">
      <button :disabled="busy" data-testid="c2-submit" @click="doSubmit">送出申請</button>
    </div>
  </template>
</div>
</template>

<style scoped>
.c2-hint {
  margin: 0.2rem 0 0;
  font-size: 0.68rem;
  line-height: 1.4;
  color: #94a3b8;
}

.c2 { display: flex; flex-direction: column; gap: 0.4rem; font-size: 0.78rem; }
.c2-seat { color: #94a3b8; font-size: 0.72rem; }
.c2-seat b { color: #7dd3fc; }
.c2-tabs { display: flex; gap: 0.3rem; }
.c2-tabs button {
  flex: 1 1 auto; padding: 0.2rem 0.4rem; font-size: 0.72rem;
  border: 1px solid #334155; border-radius: 0.25rem;
  background: transparent; color: #94a3b8; cursor: pointer;
}
.c2-tabs button.on { border-color: #2563eb; color: #e2e8f0; background: #1e293b; }
.c2-err { color: #f87171; margin: 0; font-size: 0.72rem; }
.c2-inbox-hd { display: flex; align-items: center; gap: 0.4rem; font-size: 0.7rem; color: #94a3b8; }
.c2-inbox-hd button {
  margin-left: auto; padding: 0.1rem 0.4rem; font-size: 0.68rem;
  border: 1px solid #334155; border-radius: 0.2rem;
  background: transparent; color: #cbd5e1; cursor: pointer;
}
.c2-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.c2-list li { padding: 0.3rem 0.4rem; border: 1px solid #1e293b; border-radius: 0.25rem; }
/* 未讀＝左緣加粗（不靠顏色單獨表意，投影幕上也分得出來）。 */
.c2-list li.unread { border-left: 3px solid #fcd34d; }
.c2-list .empty { color: #64748b; border: 0; }
.m-hd { display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; font-size: 0.7rem; }
.m-kind { color: #93c5fd; }
.m-from { color: #e2e8f0; font-weight: 600; }
.m-seat { color: #7dd3fc; }
.m-to { color: #fca5a5; }
.m-read { padding: 0 0.25rem; border-radius: 0.2rem; background: #1e293b; color: #fcd34d; }
.m-read.is-read { color: #86efac; }
.m-tick { margin-left: auto; color: #64748b; font-variant-numeric: tabular-nums; }
.m-body { color: #cbd5e1; margin-top: 0.15rem; }
.r-decided { margin-top: 0.15rem; font-size: 0.68rem; color: #86efac; }
.dim { color: #64748b; }
.r-st { padding: 0 0.25rem; border-radius: 0.2rem; background: #1e293b; }
.r-st.st-APPROVED { color: #86efac; }
.r-st.st-DENIED { color: #fca5a5; }
.r-st.st-EXPENDED { color: #94a3b8; }
.r-st.st-PENDING { color: #fcd34d; }
.r-act { display: flex; gap: 0.3rem; margin-top: 0.25rem; }
.r-act button {
  padding: 0.1rem 0.5rem; font-size: 0.7rem;
  border: 1px solid #334155; border-radius: 0.2rem;
  background: transparent; color: #cbd5e1; cursor: pointer;
}
/* 多了跨陣營選單後，窄工具視窗裡塞不下一列——允許換行，否則輸入框會被擠成沒有寬度。 */
.c2-compose { display: flex; gap: 0.3rem; flex-wrap: wrap; }
.c2-compose input { flex: 1 1 6rem; min-width: 0; }
.c2-compose input, .c2-sel, .c2-compose button {
  padding: 0.2rem 0.35rem; font-size: 0.72rem;
  border: 1px solid #334155; border-radius: 0.25rem;
  background: #1e293b; color: #e2e8f0;
}
.c2-compose button { cursor: pointer; }
.c2-compose button:disabled { opacity: 0.5; cursor: default; }
.c2-quota { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 0.5rem; font-size: 0.7rem; color: #94a3b8; }
.c2-quota b { color: #e2e8f0; }
</style>
