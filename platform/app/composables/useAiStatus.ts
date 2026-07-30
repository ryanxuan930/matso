// #79 AI 決策心跳狀態（COP「思考中／下一次決策倒數」）。
// 伺服端每 8s 權威重抓；本地每秒遞減 idle 倒數以平滑顯示。faction-scoped：後端已過濾，一般角色只回己方。
import { onScopeDispose, ref } from 'vue'
import type { components } from '~/types/api'
import { apiFetch } from '~/composables/useApi'

export type AiFactionStatus = components['schemas']['AiFactionStatus']

const POLL_MS = 8_000

/** 後端 heartbeat_s 缺值時的預設（與 core/app/api/autonomy.py 的 45.0 一致）。 */
const DEFAULT_HEARTBEAT_S = 45

/**
 * 「思考過久」警示門檻的下限（秒）。門檻取 max(心跳, 此值)：
 * 思考時間超過一個心跳週期就代表 AI 已落後排程（LLM 後端過慢或卡死），值得警示。
 * 心跳被設得極短（例如 10s）時用下限，免得正常的 LLM 回應時間也一直亮紅。
 */
const STALL_FLOOR_S = 30

export function useAiStatus(sessionId: () => string) {
  const factions = ref<AiFactionStatus[]>([])
  let poll: ReturnType<typeof setInterval> | null = null
  let tick: ReturnType<typeof setInterval> | null = null

  async function fetchStatus() {
    try {
      const r = await apiFetch<{ factions: AiFactionStatus[] }>(
        `/sessions/${sessionId()}/ai-status`,
      )
      factions.value = r.factions ?? []
    } catch {
      // 無 AI 指派 / 非參與者 / 尚未起跑 → 靜默隱藏狀態列。
      factions.value = []
    }
  }

  function start() {
    if (!import.meta.client || poll) return
    void fetchStatus()
    poll = setInterval(() => void fetchStatus(), POLL_MS)
    tick = setInterval(() => {
      // 本地平滑倒數（下次權威重抓會校正）。
      for (const f of factions.value) {
        if (f.state === 'idle' && typeof f.seconds_until_next === 'number') {
          f.seconds_until_next = Math.max(0, f.seconds_until_next - 1)
        }
        // 思考中的已歷時也要每秒往上走：只靠 8s 一次的權威重抓，畫面會有 8 秒是凍住的，
        // 「卡了 5 分鐘」與「剛開始思考」看起來就一樣慢——那正是這個欄位要解決的問題。
        if (f.state === 'thinking' && typeof f.thinking_since_s === 'number') {
          f.thinking_since_s = f.thinking_since_s + 1
        }
      }
    }, 1_000)
  }

  function stop() {
    if (poll) clearInterval(poll)
    if (tick) clearInterval(tick)
    poll = tick = null
  }

  onScopeDispose(stop)
  return { factions, start, stop, fetchStatus }
}

/** 秒 → mm:ss（供倒數顯示）。 */
export function formatCountdown(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.round(seconds ?? 0))
  const mm = String(Math.floor(s / 60)).padStart(2, '0')
  const ss = String(s % 60).padStart(2, '0')
  return `${mm}:${ss}`
}

/** 思考過久的判定門檻（秒）：超過一個心跳週期即視為落後排程。 */
export function stallThresholdS(heartbeatS: number | null | undefined): number {
  const hb = typeof heartbeatS === 'number' && heartbeatS > 0 ? heartbeatS : DEFAULT_HEARTBEAT_S
  return Math.max(hb, STALL_FLOOR_S)
}

/** 一個陣營 AI 狀態的顯示模型（把後端欄位換算成可直接上畫面的字串／旗標）。 */
export interface AiStatusDetail {
  faction: string
  state: AiFactionStatus['state']
  /** 中文狀態詞（統裁/參謀用語，非遊戲用語）。 */
  stateLabel: string
  /** idle：距下一次決策（mm:ss）。 */
  countdown: string
  /** thinking：本次已思考多久（mm:ss）；非 thinking 為 null。 */
  thinkingFor: string | null
  /** 思考已超過門檻 → 畫面應給視覺警示（LLM 後端可能過慢或卡死）。 */
  stalled: boolean
  /** 距上一次決策多久（mm:ss）。後端只給「距下一次」，由心跳反推。 */
  sinceLastDecision: string | null
  /** 累計決策週期數（AI 到底跑了幾輪）。 */
  cycles: number | null
  /** 上一週期落單數（**是道數不是時間**——後端 last_submitted = len(bridge.submitted)）。 */
  lastSubmitted: number | null
  /** 心跳（秒），供 UI 說明門檻由來。 */
  heartbeatS: number
}

/**
 * 把 AiFactionStatus 換算成顯示模型。
 *
 * 存在的理由：`thinking_since_s` / `cycles` / `last_submitted` 三個後端早就在送的欄位，
 * 前端一直零讀取——結果「AI 卡了 5 分鐘」與「卡了 5 秒」畫面完全一樣，也看不出 AI 到底
 * 送出過幾道令。這裡集中換算，讓 COP 狀態列與自主主控台共用同一套判讀，不會兩邊各長一套。
 */
export function describeAiStatus(f: AiFactionStatus): AiStatusDetail {
  const heartbeatS
    = typeof f.heartbeat_s === 'number' && f.heartbeat_s > 0 ? f.heartbeat_s : DEFAULT_HEARTBEAT_S
  const thinking = f.state === 'thinking'
  const elapsed = typeof f.thinking_since_s === 'number' ? f.thinking_since_s : null
  // 後端只回「距下一次決策」；上一次決策的時間 = 心跳 − 剩餘倒數（idle 時才有意義）。
  const since
    = f.state === 'idle' && typeof f.seconds_until_next === 'number'
      ? Math.max(0, heartbeatS - f.seconds_until_next)
      : null
  return {
    faction: f.faction,
    state: f.state,
    stateLabel: thinking ? '思考中' : f.state === 'idle' ? '待命' : '離線',
    countdown: formatCountdown(f.seconds_until_next),
    thinkingFor: thinking ? formatCountdown(elapsed ?? 0) : null,
    stalled: thinking && (elapsed ?? 0) >= stallThresholdS(heartbeatS),
    sinceLastDecision: since === null ? null : formatCountdown(since),
    cycles: typeof f.cycles === 'number' ? f.cycles : null,
    lastSubmitted: typeof f.last_submitted === 'number' ? f.last_submitted : null,
    heartbeatS,
  }
}
