import type { FeaturePoint } from '@/types/api'

export interface FeatureWindowSummary {
  announceTotal: number
  withdrawTotal: number
  updateTotal: number
  withdrawRate: number | null
  peakUpdates: number | null
  peakTime: string | null
  observedPoints: number
}

export function summarizeFeatureWindow(points: FeaturePoint[]): FeatureWindowSummary {
  let announceTotal = 0
  let withdrawTotal = 0
  let peakUpdates: number | null = null
  let peakTime: string | null = null
  let observedPoints = 0

  for (const point of points) {
    if (point.announce === null && point.withdraw === null) continue

    const announce = point.announce ?? 0
    const withdraw = point.withdraw ?? 0
    const updates = announce + withdraw
    announceTotal += announce
    withdrawTotal += withdraw
    observedPoints += 1

    if (peakUpdates === null || updates > peakUpdates) {
      peakUpdates = updates
      peakTime = point.time
    }
  }

  const updateTotal = announceTotal + withdrawTotal
  return {
    announceTotal,
    withdrawTotal,
    updateTotal,
    withdrawRate: updateTotal > 0 ? withdrawTotal / updateTotal : null,
    peakUpdates,
    peakTime,
    observedPoints,
  }
}
