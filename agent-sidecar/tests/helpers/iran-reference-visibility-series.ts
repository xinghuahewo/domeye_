import type { VisibilitySlot } from '../../src/domain/contracts.js'

const WINDOW_START_MS = Date.parse('2026-02-28T10:05:00Z')
const INTERVAL_MS = 5 * 60 * 1000
const SLOT_COUNT = 60
const PREFIX_VP_DENOMINATOR = 384_767
const IPV4_PREFIX_VP_DENOMINATOR = 383_804
const IPV6_PREFIX_VP_DENOMINATOR = 963
const ORIGIN_ASN_DENOMINATOR = 563

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

function visibleCount(index: number): number {
  const early = [367_215, 366_800, 366_300, 365_800, 365_334, 329_528]
  if (index < early.length) return early[index]!
  if (index <= 54) {
    return Math.round(
      329_528 + (316_733 - 329_528) * (index - 5) / (54 - 5),
    )
  }
  return [330_703, 331_300, 332_000, 332_500, 333_938][index - 55]!
}

export function iranReferenceVisibilitySeries(): VisibilitySlot[] {
  const counts = Array.from({ length: SLOT_COUNT }, (_, index) =>
    visibleCount(index),
  )
  return counts.map((count, index) => {
    const previousCount = index === 0 ? null : counts[index - 1]!
    const partiallyVisibleAsnCount = index === 7 ? 188 : 100
    const fullyInvisibleAsnCount = index === 45 ? 87 : 20
    const fullyVisibleAsnCount =
      ORIGIN_ASN_DENOMINATOR -
      partiallyVisibleAsnCount -
      fullyInvisibleAsnCount
    const ipv6VisibleCount = index === 57 ? 918 : index === 54 ? 919 : 950
    const ipv4VisibleCount = count - ipv6VisibleCount
    const announceCount = index === 0
      ? 0
      : index === 5
        ? 999_569
        : 70
    const withdrawCount = index === 0
      ? 0
      : index === 5
        ? 85_317
        : 30
    const updateTotal = announceCount + withdrawCount
    return {
      observed_at_utc: utcTimestamp(index),
      observed_at_local: localTimestamp(index),
      slot_state: 'observed',
      missing_reason: null,
      visible_prefix_vp_count: count,
      visible_prefix_vp_ratio: count / PREFIX_VP_DENOMINATOR,
      ...(previousCount === null
        ? {}
        : {
            visible_prefix_vp_delta: count - previousCount,
            visible_prefix_vp_ratio_delta_pp:
              (count - previousCount) / PREFIX_VP_DENOMINATOR * 100,
          }),
      visible_origin_asn_count:
        fullyVisibleAsnCount + partiallyVisibleAsnCount,
      fully_visible_asn_count: fullyVisibleAsnCount,
      partially_visible_asn_count: partiallyVisibleAsnCount,
      fully_invisible_asn_count: fullyInvisibleAsnCount,
      ipv4_visible_prefix_vp_count: ipv4VisibleCount,
      ipv4_visible_prefix_vp_ratio:
        ipv4VisibleCount / IPV4_PREFIX_VP_DENOMINATOR,
      ipv6_visible_prefix_vp_count: ipv6VisibleCount,
      ipv6_visible_prefix_vp_ratio:
        ipv6VisibleCount / IPV6_PREFIX_VP_DENOMINATOR,
      announce_count: announceCount,
      withdraw_count: withdrawCount,
      update_total: updateTotal,
      withdraw_ratio:
        updateTotal === 0 ? null : withdrawCount / updateTotal,
    }
  })
}
