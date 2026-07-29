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
import { computed, onMounted, ref } from 'vue'
import {
  KINDS_NEEDING_TARGET,
  MESSAGE_KIND_LABELS,
  REQUEST_KIND_LABELS,
  REQUEST_STATUS_LABELS,
  decideRequest,
  fetchMessages,
  fetchRequests,
  sendMessage,
  submitRequest,
  type MessageView,
  type RequestKind,
  type RequestList,
} from '~/composables/useC2'
import { SEAT_ROLE_LABELS } from '~/composables/useParticipants'

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

const tab = ref<'inbox' | 'requests'>('inbox')
const messages = ref<MessageView[]>([])
const reqs = ref<RequestList | null>(null)
const busy = ref(false)
const err = ref('')
const draft = ref('')
const draftSeat = ref('')
const reqKind = ref<RequestKind>('AIR_RECON')
const reqNote = ref('')

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
onMounted(reload)

async function doSend() {
  if (!draft.value.trim()) return
  busy.value = true
  try {
    await sendMessage(props.sessionId, draft.value, { toSeat: draftSeat.value || null })
    draft.value = ''
    await reload()
  } catch (e) {
    err.value = `送信失敗：${(e as { message?: string }).message ?? 'UNKNOWN'}`
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
    <ul class="c2-list" data-testid="c2-messages">
      <li v-for="m in messages" :key="m.id" data-testid="c2-message">
        <div class="m-hd">
          <span class="m-kind">{{ MESSAGE_KIND_LABELS[m.kind] ?? m.kind }}</span>
          <span class="m-from">{{ m.from_username }}</span>
          <span v-if="m.to_seat" class="m-to">→ {{ SEAT_ROLE_LABELS[m.to_seat] ?? m.to_seat }}</span>
          <span v-else class="m-to dim">→ 全軍</span>
          <span class="m-tick">T{{ m.tick }}</span>
        </div>
        <div class="m-body">{{ m.body }}</div>
      </li>
      <li v-if="!messages.length" class="empty">（無信文）</li>
    </ul>
    <div class="c2-compose">
      <select v-model="draftSeat" class="c2-sel" data-testid="c2-to-seat">
        <option value="">全軍</option>
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
          <span class="m-tick">T{{ r.requested_at_tick }}</span>
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
.c2-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 0.25rem; }
.c2-list li { padding: 0.3rem 0.4rem; border: 1px solid #1e293b; border-radius: 0.25rem; }
.c2-list .empty { color: #64748b; border: 0; }
.m-hd { display: flex; align-items: center; gap: 0.35rem; flex-wrap: wrap; font-size: 0.7rem; }
.m-kind { color: #93c5fd; }
.m-from { color: #e2e8f0; font-weight: 600; }
.m-to { color: #fca5a5; }
.m-tick { margin-left: auto; color: #64748b; font-variant-numeric: tabular-nums; }
.m-body { color: #cbd5e1; margin-top: 0.15rem; }
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
.c2-compose { display: flex; gap: 0.3rem; }
.c2-compose input { flex: 1 1 auto; min-width: 0; }
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
