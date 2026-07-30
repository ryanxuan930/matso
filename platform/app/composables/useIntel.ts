// 敵情（fog of war）查詢與投影（#90，SPEC §7.2/§13.3）——contact 一律取自後端偵測結果。
//
// 紅線 #3：**過濾只在後端**。本檔不做任何「哪些看得到」的判斷，只把後端已去識別化的
// ContactView 轉成地圖用的 Contact 形狀。

import type { components } from '~/types/api'
import type { Contact, Fidelity, Relation } from '~/composables/useUnits'

export type ContactView = components['schemas']['ContactView']
export type FactionRelationsView = components['schemas']['FactionRelationsView']

/**
 * 取「觀測者對各陣營」的關係（#91）——決定 2525 affiliation（友/中/敵）的唯一依據。
 * 後端刻意只回以觀測者為中心的一列，不含第三方之間的結盟。
 */
export function fetchRelations(
  sessionId: string,
  asFaction?: string | null,
): Promise<FactionRelationsView> {
  const q = asFaction ? `?as_faction=${encodeURIComponent(asFaction)}` : ''
  return apiFetch<FactionRelationsView>(`/sessions/${sessionId}/relations${q}`)
}

/**
 * 取 faction-scoped 敵情。
 * - 一般角色：不帶 `asFaction`＝自身陣營視角（帶他陣營→後端 403）。
 * - 全知角色：不帶＝god view（所有陣營的 contacts）；帶＝以該陣營視角（#90 視角切換）。
 */
export function fetchIntel(sessionId: string, asFaction?: string | null): Promise<ContactView[]> {
  const q = asFaction ? `?as_faction=${encodeURIComponent(asFaction)}` : ''
  return apiFetch<ContactView[]>(`/sessions/${sessionId}/intel${q}`)
}

/**
 * ContactView（後端投影）→ 地圖 Contact。
 *
 * `relationFor` 由呼叫端提供（觀測者對該陣營的關係）——本檔不自行假設敵我：faction 只在
 * IDENTIFIED 才揭露，未揭露時無從判關係，統一交由呼叫端決定預設。
 */
export function toContact(
  view: ContactView,
  relationFor?: (faction?: string | null) => Relation | undefined,
): Contact {
  return {
    contactId: view.contact_id,
    fidelity: view.fidelity as Fidelity,
    lat: view.lat,
    lng: view.lng,
    errorRadiusM: view.error_radius_m,
    lastSeenTick: view.last_seen_tick,
    // 兵科 → 符號的 function ID。⚠ 這一欄後端過去叫 `unit_type` 但裝的是**階層**，
    // 拿去查兵科表永遠 miss（所以敵情 contact 一直也沒有兵種圖示）。現已改名分家。
    ...(view.branch ? { unitType: view.branch } : {}),
    ...(view.echelon ? { echelon: view.echelon } : {}),
    ...(view.designation ? { designation: view.designation } : {}),
    ...(view.faction ? { faction: view.faction } : {}),
    ...(relationFor ? { relation: relationFor(view.faction) } : {}),
  }
}
