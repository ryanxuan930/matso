/**
 * COP 戰況/指令列的文字格式化——把裁決事件與指令轉成指揮官讀得懂的一行字。
 *
 * 這些都是純函數，唯一的外部相依是「單位清單」（要把 UUID 換成番號），故以 getter 收下，
 * 讓戰況事件與指令兩個小工具共用同一份 ID→番號解析，不會各自解一套而顯示不一致。
 */
import { commsLabel } from '~/composables/useUnits'
import { orderTypeLabel, type OrderResponse, type UnitView } from '~/composables/useOrders'

export function useCopFeed(units: () => UnitView[]) {
  // 事件 → 可讀文字（ID→番號、交戰命中/未命中/戰損）。供戰況 feed 即時回饋（含多機同步）。
  function unitName(id?: unknown): string {
    const s = typeof id === 'string' ? id : ''
    return (s && units().find((u) => u.id === s)?.designation) || s
  }
  // #27 指令對象：ENGAGE→目標單位；MOVE→目的地 hex（供指令列顯示被下令對象）。
  function orderTargetLabel(o: OrderResponse): string {
    if (o.order_type === 'ENGAGE' && o.target_unit_id) {
      const name = units().find((u) => u.id === o.target_unit_id)?.designation
      return `→ ${name ?? '敵目標'}`
    }
    if (o.order_type === 'MOVE' && o.target_h3) return `→ ${o.target_h3.slice(0, 9)}`
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
        const why = payload?.reason_detail || payload?.reason || ''
        return `交戰不可行 ${ini} → ${tgt}（${why}）`
      }
      return `交戰 ${ini} → ${tgt}`
    }
    if (type === 'UNIT_ARRIVED') return `${ini} 已抵達目標`
    if (type === 'MOVE_ATTRITION') {
      const dmg = payload?.damage != null ? Math.round(Number(payload.damage)) : ''
      return `${ini} 強穿阻礙受損 −${dmg}`
    }
    if (type === 'COMMS_STATE_CHANGED') {
      return `${ini} 通聯 ${commsLabel(String(payload?.from ?? ''))}→${commsLabel(String(payload?.to ?? ''))}`
    }
    const ot = payload?.order_type ? ` · ${orderTypeLabel(String(payload.order_type))}` : ''
    return `${type}${ot}`
  }

  return { unitName, orderTargetLabel, formatEvent }
}
