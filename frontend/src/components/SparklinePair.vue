<script setup lang="ts">
import { computed } from 'vue'

import type { CountrySparkPoint } from '@/types/api'

const props = withDefaults(defineProps<{
  points: CountrySparkPoint[]
  label?: string
}>(), {
  label: 'ANNOUNCE 与 WITHDRAW 小型趋势',
})

const width = 132
const height = 42
const padding = 3

function polyline(field: 'announce' | 'withdraw') {
  if (props.points.length === 0) return ''
  const values = props.points.flatMap((point) => [point.announce, point.withdraw])
  const ceiling = Math.max(1, ...values)
  const span = Math.max(1, props.points.length - 1)
  return props.points.map((point, index) => {
    const x = padding + index / span * (width - padding * 2)
    const y = height - padding - point[field] / ceiling * (height - padding * 2)
    return `${x.toFixed(1)},${y.toFixed(1)}`
  }).join(' ')
}

const announcePoints = computed(() => polyline('announce'))
const withdrawPoints = computed(() => polyline('withdraw'))
</script>

<template>
  <svg
    class="sparkline-pair"
    :viewBox="`0 0 ${width} ${height}`"
    role="img"
    :aria-label="label"
    preserveAspectRatio="none"
  >
    <path class="spark-grid" d="M3 21H129" />
    <polyline v-if="announcePoints" class="announce-line" :points="announcePoints" />
    <polyline v-if="withdrawPoints" class="withdraw-line" :points="withdrawPoints" />
  </svg>
</template>

<style scoped>
.sparkline-pair {
  display: block;
  width: 132px;
  height: 42px;
  overflow: visible;
}

.spark-grid {
  fill: none;
  stroke: #e6ebf1;
  stroke-dasharray: 2 3;
  vector-effect: non-scaling-stroke;
}

.announce-line,
.withdraw-line {
  fill: none;
  stroke-linecap: round;
  stroke-linejoin: round;
  stroke-width: 1.6;
  vector-effect: non-scaling-stroke;
}

.announce-line {
  stroke: var(--primary);
}

.withdraw-line {
  stroke: var(--cyan);
}
</style>
