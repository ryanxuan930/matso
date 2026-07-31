/**
 * COP 戰況/指令列的文字格式化——把裁決事件與指令轉成指揮官讀得懂的一行字。
 *
 * 這些都是純函數，唯一的外部相依是「單位清單」（要把 UUID 換成番號），故以 getter 收下，
 * 讓戰況事件與指令兩個小工具共用同一份 ID→番號解析，不會各自解一套而顯示不一致。
 *
 * ## 這個檔案在補的洞
 *
 * 後端會發約 46 種事件，本檔原本只翻譯 7 種，其餘全部落到退路只印型別字串。
 * 於是戰況小工具會冒出一排 `MOVE_HALTED_FUEL` / `GUARDRAIL_INTERVENTION` / `MINE_STRIKE`
 * ——**而且不帶番號**。指揮官看到「MOVE_HALTED_FUEL」根本不知道是哪一支部隊沒油了。
 *
 * ## 可用的 payload 欄位比想像中少（很重要，別憑空生資料）
 *
 * 進到 WS 戰況流的不是整包 `LedgerEvent`，而是 `core/app/state/broadcaster.py`
 * 的 `build_event_envelope` 壓過的精簡版：只有 `event_type` / `tick` / `initiator_id` /
 * `target_id` / `damage`，加上 **`ai_decision` 的白名單鍵**（status、reason、reason_detail、
 * target_health_after、from、to、mode、winners、observation、rounds、estimated_losses、
 * is_estimate、error_band、cause、shooter_faction）。
 *
 * `LedgerEvent.detail` 過去**完全不下發**，所以 `MOVE_HALTED_FUEL` 的剩油量、
 * `MOVE_ATTRITION` 的里程與機動 profile、觸雷打到哪一道障礙，前端一概拿不到。
 * 現在 `broadcaster._DETAIL_KEYS` 轉發了一份白名單（見 `detailsOf`），
 * **但 `lat` / `lng` 刻意不在其中**——下發會繞過 WP-C5 的位置凍結。
 *
 * 白名單之外的鍵仍然拿不到。這裡刻意不去「補完」看起來該有的細節：
 * 在兵推系統裡編造一個聽起來合理的原因，比留白危險得多。
 */
import { commsLabel } from '~/composables/useUnits'
import {
  missionTypeLabel,
  orderTypeLabel,
  type OrderResponse,
  type UnitView,
} from '~/composables/useOrders'

/**
 * 事件型別 → 中文基本敘述（不含番號；番號由 `formatEvent` 依 initiator/target 補上）。
 *
 * ⚠ **後端新增 event_type 時一定要回來補這張表。** 沒補的型別會落到 `formatEvent` 的
 * 安全退路，戰況小工具就又冒出裸英文代號。這條約束由
 * `core/tests/unit/test_event_labels_coverage.py` 守著：它掃 `core/app` 全部的
 * `event_type` 字面量，逐一斷言出現在本表或 `EVENT_TYPES_NOT_IN_FEED` 內。
 */
export const EVENT_LABELS: Record<string, string> = {
  // ---- 交戰/火力 ----
  ENGAGEMENT_RESOLVED: '交戰',
  AGGREGATE_ENGAGEMENT_RESOLVED: '聚合接戰',
  AREA_FIRE_RESOLVED: '面射擊',
  BDA_REPORT: '戰果評估',
  FRATRICIDE: '友軍誤傷',
  SMOKE_EMPLACED: '施放煙幕',
  SUPPLY_POINT_DESTROYED: '摧毀敵補給點',
  ORDER_RESTRICTED_FIRE_OVERRIDE: '限制射擊區射擊覆寫',
  // ---- 機動 ----
  UNIT_ARRIVED: '已抵達目標',
  UNIT_MOVED: '位置更新', // 見 EVENT_TYPES_NOT_IN_FEED：正常情況不會出現在 feed
  MOVE_ATTRITION: '行進耗損',
  MOVE_BLOCKED: '移動受阻',
  MOVE_HALTED_FUEL: '燃料耗盡，就地停止',
  MOVE_ROUTE_PLANNED: '路線已規劃',
  MOVE_ROUTE_FALLBACK: '路線規劃失敗，改走直線',
  MINE_STRIKE: '觸雷',
  SURVIVABILITY_MOVE: '陣地變換',
  SURVIVABILITY_MOVE_BLOCKED: '陣地變換受阻',
  // ---- 工兵/障礙 ----
  ENGINEER_WORK_STARTED: '工兵作業開始',
  ENGINEER_WORK_ABORTED: '工兵作業中止',
  OBSTACLE_EMPLACED: '完成設障',
  OBSTACLE_BREACHED: '完成破障',
  // ---- 後勤/整補 ----
  RESUPPLIED: '自補給點受補',
  RESUPPLY_TICK: '補給輸送中',
  RESUPPLY_COMPLETED: '補給完成',
  RESUPPLY_FAILED: '補給失敗',
  REFIT_STARTED: '開始整補',
  REFIT_PROGRESS: '整補中',
  REFIT_BLOCKED: '整補受阻',
  // ---- 情報/通聯/環境 ----
  SENSOR_CONTACT: '偵獲接觸',
  COMMS_STATE_CHANGED: '通聯狀態改變',
  WEATHER_STALE: '氣象資料逾期（沿用最後一次觀測）',
  WEATHER_FRESH: '氣象資料已更新',
  // ---- 任務級下令（WP-A2）----
  MISSION_ENDED: '任務結束',
  MISSION_SUBORDER_REJECTED: '任務子令被拒',
  // ---- 白軍/MSEL/系統 ----
  MSEL_MESSAGE: '收到演習注入信文',
  MSEL_PAUSE: '演習注入：推演暫停',
  MSEL_UNITS_SPAWNED: '演習注入：增援單位加入戰場',
  MSEL_UNIT_MODIFIED: '演習注入：單位狀態經白軍調整',
  MSEL_WEATHER_OVERRIDE: '演習注入：氣象條件覆蓋',
  MSEL_INJECT_FAILED: '演習注入失敗',
  MSEL_INJECT_UNSUPPORTED: '演習注入未支援',
  SESSION_CONTROL: '白軍時間控制',
  SESSION_CONCLUDED: '推演結束',
  FACTION_RELATION_CHANGED: '陣營關係變更',
  GUARDRAIL_INTERVENTION: 'AI 護欄攔截',
  ROLLBACK: '推演回滾',
  TICK_OVERRUN: 'tick 逾時', // 見 EVENT_TYPES_NOT_IN_FEED：診斷用，不進 feed
  // ---- C2 信文/申請核覆（WP-B5.2）。走 stream/publish.py 而非 broadcaster，
  //      所以整包 payload 都下發得到；但這裡只講「有新東西」，內文請進 C2 小工具讀。 ----
  C2_MESSAGE: '收到 C2 信文',
  C2_MESSAGE_READ: 'C2 信文已閱',
  C2_REQUEST: '收到申請案，待核覆',
  // E2E stub 模式（settings.stub_gateway）才會發：真裁決由 kernel 產出。
  ORDER_VALIDATED: '指令已驗證',
  // 人工下令被預檢擋下。**與 AI 護欄的 GUARDRAIL_INTERVENTION 分開**：
  // 檢討時要分得出「人下了被擋」與「AI 想下被剔除」，那是兩種完全不同的事。
  ORDER_REJECTED: '指令被拒（預檢未過）',
  REQUEST_SUBMITTED: '提出申請',
  REQUEST_DECIDED: '申請已核覆',
}

/**
 * 後端**刻意不推進戰況流**的事件型別——必須與 `broadcaster.py` 的 `_FEED_EXCLUDE` 一致。
 *
 * `UNIT_MOVED` 每 tick 每移動單位一則（會洗版，位置改由 STATE_DIFF 呈現）；
 * `TICK_OVERRUN` 是效能診斷，不是戰場事實。上表仍給了它們標籤（萬一哪天解除排除
 * 也不會變裸代號），但這份清單才是「知道它不該出現在這裡」的宣告。
 */
export const EVENT_TYPES_NOT_IN_FEED: readonly string[] = ['UNIT_MOVED', 'TICK_OVERRUN']

/**
 * 後端的原因代碼 → 中文。
 *
 * 代碼是後端自由字串（各子系統各自定義），不是封閉 enum，所以查不到就**原樣印代號**
 * ——印代號至少能被搜尋與追查，猜一個中文只會誤導。
 */
const REASON_LABELS: Record<string, string> = {
  // 交戰/火力
  NO_LOS: '無視線',
  TRAJECTORY_BLOCKED: '彈道受阻',
  OUT_OF_RANGE: '超射程',
  NO_AMMO: '無彈藥',
  NO_INDIRECT_WEAPON: '無可用曲射武器',
  ROE: '交戰規則禁止',
  POLICY: '火力政策限制',
  HOLD_FIRE: '火力政策暫停射擊',
  // 機動
  OUT_OF_FUEL: '燃料耗盡',
  IMPASSABLE_TERRAIN: '地形不可通行',
  MARCH: '行軍',
  FORCED_CROSSING: '強行穿越障礙',
  COUNTER_BATTERY: '反砲兵威脅',
  NO_REACHABLE_POSITION: '無可用備用陣地',
  // 工兵/後勤/整補
  TARGET_GONE: '標的已不存在',
  NO_TARGET: '找不到受補單位',
  NOT_SAME_FACTION: '非同陣營',
  CARGO_EMPTY: '載運量耗盡',
  TARGET_FULL: '受補單位已滿載',
  UNDER_ATTACK: '遭敵接戰中',
  NO_SUPPLY_POINT: '無可用補給點',
  ENEMY_NEAR: '敵軍過近',
  NO_PARTS: '缺乏料件',
}

/** 白軍時間控制動作 → 中文（`SESSION_CONTROL.action`）。 */
export const CONTROL_ACTION_LABELS: Record<string, string> = {
  PAUSE: '暫停推演',
  RESUME: '恢復推演',
  ROLLBACK: '回滾至先前存檔點',
}

export function reasonLabel(code?: unknown): string {
  const s = typeof code === 'string' ? code : ''
  return s ? (REASON_LABELS[s] ?? s) : ''
}

/**
 * 事件的「為什麼」。`reason_detail` 是已組好的人話（如逐武器原因彙總），優先；
 * 退回 `reason` 代碼並翻成中文；兩者皆無 → 空字串（**不編一個原因出來**）。
 */
function whyOf(payload: Record<string, unknown>): string {
  const detail = payload?.reason_detail
  if (typeof detail === 'string' && detail) return detail
  return reasonLabel(payload?.reason)
}

/** 機動 profile 與行軍節奏的中文（未知代號原樣印，不編一個出來）。 */
const PROFILE_LABELS: Record<string, string> = {
  FOOT: '徒步',
  WHEELED: '輪型',
  TRACKED: '履帶',
}
const TEMPO_LABELS: Record<string, string> = {
  CAUTIOUS: '謹慎',
  NORMAL: '常速',
  FORCED: '強行軍',
}
/** 工兵作業別（`obstacle_wiring` 的 `detail.action`）。 */
const ENGINEER_ACTION_LABELS: Record<string, string> = {
  BREACH: '破障',
  EMPLACE: '設障',
}

function num(v: unknown, digits = 1): string | undefined {
  return typeof v === 'number' && Number.isFinite(v) ? v.toFixed(digits) : undefined
}

/**
 * 事件的**具體內容**——走了多遠、剩多少油、掉了多少戰力、破的是哪一道障礙。
 *
 * 這些鍵住在 `LedgerEvent.detail`。後端曾經**完全不下發 detail**，於是移動、工兵、
 * 後勤事件的「為什麼」在整個系統裡沒有任何操作員取得得到的路徑：即時串流沒有、
 * AAR 畫面沒有、匯出檔也沒有。`broadcaster._DETAIL_KEYS` 已經把安全的那些轉發出來
 * （`lat`/`lng` 刻意不在其中——那會繞過 WP-C5 的位置凍結），本函式是消費端。
 *
 * **只印真的收到的欄位**，缺的一律略過。「行進耗損 −3.2」與
 * 「行進耗損 −3.2（2.4 km · 履帶 · 強行軍）」差的是指揮官能不能判斷
 * 那是地形磨的還是自己催出來的。
 */
function detailsOf(payload: Record<string, unknown>): string {
  const bits: string[] = []
  const km = num(payload?.distance_km)
  if (km) bits.push(`${km} km`)
  const profile = typeof payload?.profile === 'string' ? payload.profile : ''
  if (profile) bits.push(PROFILE_LABELS[profile] ?? profile)
  const tempo = typeof payload?.tempo === 'string' ? payload.tempo : ''
  if (tempo) bits.push(TEMPO_LABELS[tempo] ?? tempo)
  const before = num(payload?.strength_before)
  const after = num(payload?.strength_after)
  if (before && after) bits.push(`戰力 ${before}→${after}`)
  const fuel = num(payload?.fuel_remaining)
  if (fuel) bits.push(`剩油 ${fuel}`)
  const burn = num(payload?.fuel_burn_per_km)
  if (burn) bits.push(`每公里 ${burn}`)
  // 障礙：想定給的 label 是人話（「1 號雷區」），沒有就退回 id 前 8 碼。
  const label = typeof payload?.label === 'string' ? payload.label : ''
  const feature = typeof payload?.feature_id === 'string' ? payload.feature_id : ''
  if (label || feature) bits.push(label || feature.slice(0, 8))
  // 工兵在場與否會改變觸雷機率與強穿代價——這一格是「為什麼傷得比較輕」的答案。
  if (payload?.engineer === true) bits.push('工兵在場')
  // 工兵作業別。`SESSION_CONTROL` 也有 `action`，但它在上面就 return 了，不會走到這裡。
  const action = typeof payload?.action === 'string' ? payload.action : ''
  if (action) bits.push(ENGINEER_ACTION_LABELS[action] ?? action)
  const legs = payload?.legs
  if (typeof legs === 'number' && legs > 1) bits.push(`${legs} 段路線`)
  const eta = payload?.eta_tick
  if (typeof eta === 'number') bits.push(`預計 T${eta} 完成`)
  const issuedFuel = num(payload?.fuel)
  if (issuedFuel) bits.push(`撥油 ${issuedFuel}`)
  const issuedAmmo = payload?.ammo
  if (typeof issuedAmmo === 'number') bits.push(`撥彈 ${issuedAmmo}`)
  return bits.join(' · ')
}

export function useCopFeed(units: () => UnitView[]) {
  /**
   * ID → 番號。查不到（他軍單位、已離場單位、MSEL 生成的新單位還沒進清單）時
   * **只印前 8 碼**：整串 UUID 會把一行字擠爆，而前 8 碼仍足以在 AAR 裡比對。
   */
  function unitName(id?: unknown): string {
    const s = typeof id === 'string' ? id : ''
    if (!s) return ''
    return units().find((u) => u.id === s)?.designation || s.slice(0, 8)
  }
  /**
   * #27 指令對象——這一格回答「這道令是下給誰/往哪裡」。
   *
   * ⚠ 能講到什麼程度受限於 `OrderResponse`：它只回 `target_unit_id` / `target_h3` /
   * `mission_type`，**不回令載荷**。所以 FIRE_MISSION 的落點座標、ENGINEER 的作業與
   * 障礙型別、FORMATION 的隊形與乘駐車在前端根本拿不到（見本檔頭與回報的 blocked 項）。
   * 這裡不去假裝有那些資訊。
   */
  function orderTargetLabel(o: OrderResponse): string {
    // MISSION：任務型就是這道令的核心語義（奪佔/防守/掩護幕/行軍），比目的地更該先講。
    if (o.order_type === 'MISSION') {
      const mt = missionTypeLabel(o.mission_type)
      return mt ? `→ ${mt}` : ''
    }
    // 對單位下的令（ENGAGE 打誰、RESUPPLY 補誰）。
    if (o.target_unit_id) {
      const name = unitName(o.target_unit_id)
      return name ? `→ ${name}` : ''
    }
    // 對地點下的令（MOVE/RECON 的目的地 hex）。
    if (o.target_h3) return `→ ${o.target_h3.slice(0, 9)}`
    return ''
  }
  function formatEvent(payload: Record<string, unknown>): string {
    const type = String(payload?.event_type ?? '')
    const ini = unitName(payload?.initiator_id)
    const tgt = unitName(payload?.target_id)
    if (type === 'ENGAGEMENT_RESOLVED') {
      const status = String(payload?.status ?? '')
      // 聯合兵種加總（P4）：標示「聯合火力」，讓戰況 feed 區分單武器 vs 武器組合交戰。
      const cx = payload?.mode === 'COMBINED' ? '（聯合火力）' : ''
      if (status === 'HIT') {
        const dmg = payload?.damage != null ? ` −${Math.round(Number(payload.damage))}` : ''
        const hp = Number(payload?.target_health_after)
        const after = Number.isFinite(hp) ? `（剩 ${Math.round(hp)}%）` : ''
        const ko = Number.isFinite(hp) && hp <= 0 ? ' ✖摧毀' : ''
        return `交戰命中${cx} ${ini} → ${tgt}${dmg}${after}${ko}`
      }
      if (status === 'MISS') return `交戰未命中${cx} ${ini} → ${tgt}`
      if (status === 'REJECTED') {
        // 聯合兵種：優先顯示逐武器原因彙總（如「無視線×2、超射程×1、無彈藥×1」），比單一 code 清楚。
        // 單武器交戰只有 code（NO_AMMO…），故過一次 reasonLabel 才不會印英文。
        return `交戰不可行 ${ini} → ${tgt}（${whyOf(payload)}）`
      }
      // 沒有 status 的 ENGAGEMENT_RESOLVED 只有 E2E stub 模式會發，那則沒有 initiator/target。
      // 舊寫法會印出「交戰  → 」這種懸空的箭頭；沒有對象就不要畫箭頭。
      return tgt ? `交戰 ${ini} → ${tgt}` : `交戰 ${ini}`.trim()
    }
    // WP-C10.4：面射擊只說「彈落了」——**不說打死幾個**。
    // 沒有觀測時後端根本不下發傷亡數字（feed_damage），這裡也不該憑空生一個。
    if (type === 'AREA_FIRE_RESOLVED') {
      if (payload?.status === 'REJECTED') {
        return `火力任務未執行 ${ini}（${whyOf(payload)}）`
      }
      const rounds = payload?.rounds != null ? ` ${payload.rounds} 發` : ''
      const blind = payload?.observation === 'UNOBSERVED' ? '（無觀測，散布加倍）' : ''
      return `面射擊落彈 ${ini}${rounds}${blind}`
    }
    // WP-C9 誤傷。**用最直白的字**——這一列在檢討會上要一眼認得出來，
    // 而且它的受眾已由後端收斂成射手陣營與受害陣營（見 fire_wiring._fratricide_events）。
    if (type === 'FRATRICIDE') {
      const cause = payload?.cause === 'AREA_FIRE' ? '面射擊' : '交戰'
      return `⚠ 友軍誤傷（${cause}）${ini} → ${tgt}`
    }
    // WP-C10.4b：戰果評估。**永遠標「約」與誤差帶**——這是觀測者看到的，不是事實。
    if (type === 'BDA_REPORT') {
      const est = Number(payload?.estimated_losses ?? 0)
      const band = Number(payload?.error_band ?? 0)
      const pct = band > 0 ? `±${Math.round(band * 100)}%` : ''
      return `戰果評估 ${ini} 觀測：約 −${est.toFixed(1)}（估計 ${pct}）`
    }
    // 營級聚合裁決：雙方同時計損，`damage` 是**兩軍合計**（見 adjudication/aggregate.py），
    // 不是「打掉對方多少」——寫成單方戰損會讓檢討會讀反戰況。
    if (type === 'AGGREGATE_ENGAGEMENT_RESOLVED') {
      const d = payload?.damage
      const dmg = d != null ? `，雙方合計戰損 −${Number(d).toFixed(1)}` : ''
      return `聚合接戰 ${ini} ↔ ${tgt}${dmg}`
    }
    if (type === 'COMMS_STATE_CHANGED') {
      return `${ini} 通聯 ${commsLabel(String(payload?.from ?? ''))}→${commsLabel(String(payload?.to ?? ''))}`
    }
    // 偵獲：這則事件**只有 target_id**（受眾已由後端收斂成觀測方，見 event_audience），
    // 套通用格式會變成「偵獲接觸 → X」那種沒有主詞的怪句子，故單獨處理。
    if (type === 'SENSOR_CONTACT') return `偵獲接觸：${tgt}`
    // 勝負底定。橫幅另有一份（cop.vue），feed 這一列是給事後翻紀錄的人看的。
    if (type === 'SESSION_CONCLUDED') {
      const winners = Array.isArray(payload?.winners) ? (payload.winners as string[]) : []
      return `推演結束 — ${winners.length ? `${winners.join('、')} 獲勝` : '未分勝負'}`
    }
    // 白軍時間控制：**一定要說是誰按的**（白軍），否則其他席位會以為系統掛了。
    // 圖示跟著動作走——一排都是 ⏸ 的話，恢復推演那則會被讀成又暫停了一次。
    if (type === 'SESSION_CONTROL') {
      const act = String(payload?.action ?? '')
      const icon = act === 'RESUME' ? '▶' : act === 'ROLLBACK' ? '↩' : '⏸'
      const tt = payload?.target_tick != null ? `（至 T${payload.target_tick}）` : ''
      return `${icon} 白軍時間控制：${CONTROL_ACTION_LABELS[act] ?? act}${tt}`
    }
    // 回滾：payload.tick 就是回滾到的目標 tick（見 checkpoint.py 建事件處）。
    if (type === 'ROLLBACK') {
      const t = payload?.tick != null ? `至 T${payload.tick}` : ''
      return `↩ 推演回滾${t}`
    }
    const ot = payload?.order_type ? ` · ${orderTypeLabel(String(payload.order_type))}` : ''
    const label = EVENT_LABELS[type]
    if (label) {
      // 通用格式：番號 + 敘述 +（原因）+ 戰損。
      // `reason`/`reason_detail` 只有部分事件下發得到（見本檔頭的白名單說明），
      // 拿不到就不寫——這一行**不補任何猜測**。
      const why = whyOf(payload)
      const head = ini ? `${ini} ` : ''
      const tail = tgt ? ` → ${tgt}` : ''
      const d = payload?.damage
      const dmg = d != null ? ` −${Number(d).toFixed(1)}` : ''
      // 原因與細節分開括：原因回答「為什麼」，細節回答「多少／哪一個」。
      // 兩者常同時存在（`MOVE_HALTED_FUEL` 有 reason 也有剩油量），塞進同一括號會糊掉。
      const detail = detailsOf(payload)
      const extra = `${why ? `（${why}）` : ''}${detail ? `［${detail}］` : ''}`
      return `${head}${label}${ot}${tail}${extra}${dmg}`
    }
    // 安全退路：**新增事件型別時要回來補 EVENT_LABELS**，不是靠這一行撐著。
    // 除了「後端先上線、前端隨後跟上」的空窗期，還有一種永遠補不完的情況：
    // MSEL 想定可以注入**自訂 event_type**（`msel_runtime` 直接吃 inject["event_type"]），
    // 那是白軍寫在想定檔裡的字串，不可能事先列進表裡——原樣印出就是正確行為。
    return `${type}${ot}`
  }

  return { unitName, orderTargetLabel, formatEvent }
}
