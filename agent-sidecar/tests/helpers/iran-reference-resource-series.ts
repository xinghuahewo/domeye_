import type { ResourceSlot } from '../../src/domain/contracts.js'

const WINDOW_START_MS = Date.parse('2026-02-28T10:05:00Z')
const INTERVAL_MS = 5 * 60 * 1000
const SLOT_COUNT = 60

function utcTimestamp(index: number): string {
  return new Date(WINDOW_START_MS + index * INTERVAL_MS)
    .toISOString()
    .replace('.000Z', 'Z')
}

function localTimestamp(index: number): string {
  return new Date(WINDOW_START_MS + index * INTERVAL_MS + 8 * 60 * 60 * 1000)
    .toISOString()
    .replace('.000Z', '+08:00')
}

function ipv4EquivalentCount(index: number): number {
  const opening = [39_000, 39_100, 39_200, 39_260]
  if (index < opening.length) return opening[index]!
  if (index <= 53) {
    return Math.round(
      39_260 + (37_379 - 39_260) * (index - 3) / (53 - 3),
    )
  }
  return Math.round(
    37_379 + (38_000 - 37_379) * (index - 53) / (59 - 53),
  )
}

export function iranReferenceResourceSeries(): ResourceSlot[] {
  const ipv4EquivalentCounts = Array.from(
    { length: SLOT_COUNT },
    (_, index) => ipv4EquivalentCount(index),
  )
  const ipv6EquivalentCounts = Array.from(
    { length: SLOT_COUNT },
    (_, index) => index < 30 ? 524_288 : 1_048_576,
  )
  const announceCounts = Array.from(
    { length: SLOT_COUNT },
    (_, index) =>
      index === 0 ? 0 : index === 4 ? 298_812 : 1_000 + index * 13,
  )
  const withdrawCounts = Array.from(
    { length: SLOT_COUNT },
    (_, index) =>
      index === 0 ? 0 : index === 4 ? 42_148 : 300 + index * 7,
  )

  return Array.from({ length: SLOT_COUNT }, (_, index) => {
    const ipv4Equivalent = ipv4EquivalentCounts[index]!
    const ipv6Equivalent = ipv6EquivalentCounts[index]!
    const announce = announceCounts[index]!
    const withdraw = withdrawCounts[index]!
    const updateTotal = announce + withdraw
    const previousIndex = index - 1
    return {
      observed_at_utc: utcTimestamp(index),
      observed_at_local: localTimestamp(index),
      ipv4_24_equivalent_count: ipv4Equivalent,
      ipv4_address_count: ipv4Equivalent * 256,
      ipv6_48_equivalent_count: ipv6Equivalent,
      announce_count: announce,
      withdraw_count: withdraw,
      update_total: updateTotal,
      withdraw_ratio:
        updateTotal === 0 ? null : withdraw / updateTotal,
      ipv4_24_equivalent_delta:
        index === 0
          ? null
          : ipv4Equivalent - ipv4EquivalentCounts[previousIndex]!,
      ipv4_address_delta:
        index === 0
          ? null
          : (
              ipv4Equivalent -
              ipv4EquivalentCounts[previousIndex]!
            ) * 256,
      ipv6_48_equivalent_delta:
        index === 0
          ? null
          : ipv6Equivalent - ipv6EquivalentCounts[previousIndex]!,
      announce_delta:
        index === 0
          ? null
          : announce - announceCounts[previousIndex]!,
      withdraw_delta:
        index === 0
          ? null
          : withdraw - withdrawCounts[previousIndex]!,
    }
  })
}
