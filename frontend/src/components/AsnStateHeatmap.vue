<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import type { EventObservationAsnTimeline } from '@/types/api'
import { formatTimezoneLabel } from '@/utils/eventObservationTemplate'

const props = withDefaults(defineProps<{
  rows: EventObservationAsnTimeline[]
  times: string[]
  height?: number
  timezone?: string
}>(), {
  height: 620,
  timezone: 'Asia/Shanghai',
})

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const stateLabels: Record<number, string> = {
  [-1]: '未知',
  0: '全可见',
  1: '部分可见',
  2: '全不可见',
}

const cells = computed(() => props.rows.flatMap((row, rowIndex) =>
  row.states.map((state, timeIndex) => [timeIndex, rowIndex, state]),
))

function timeLabel(value: string) {
  return value.replace(' ', 'T').slice(11, 16)
}

function escapeHtml(value: unknown) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

function renderChart() {
  if (!chartElement.value) return
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.setOption({
    animation: false,
    grid: { left: 76, right: 18, top: 12, bottom: 44 },
    tooltip: {
      position: 'top',
      confine: true,
      borderWidth: 1,
      borderColor: '#cfd7df',
      backgroundColor: 'rgba(255,255,255,.98)',
      extraCssText: 'box-shadow:0 10px 28px rgba(18,39,53,.14);border-radius:3px;',
      textStyle: { color: '#172632', fontSize: 11 },
      formatter: (params: { value?: [number, number, number] }) => {
        const [timeIndex, rowIndex, state] = params.value || []
        const row = typeof rowIndex === 'number' ? props.rows[rowIndex] : undefined
        const time = typeof timeIndex === 'number' ? props.times[timeIndex] : undefined
        const label = typeof state === 'number' ? stateLabels[state] : '未知'
        return [
          `<b style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">AS${escapeHtml(row?.asn || '—')}</b>`,
          `<div style="margin-top:5px;color:#607080">${escapeHtml(time ? `${timeLabel(time)} · ${formatTimezoneLabel(props.timezone)}` : '时间未知')}</div>`,
          `<div style="margin-top:5px">${escapeHtml(label)}</div>`,
          `<div style="margin-top:6px;padding-top:6px;border-top:1px solid #e3e8ec;color:#607080">地址族 ${escapeHtml(row?.address_families.join(' / ') || '未知')} · 固定路由观测关系 ${escapeHtml(row?.baseline_prefix_vp_count ?? '—')}</div>`,
        ].join('')
      },
    },
    xAxis: {
      type: 'category',
      data: props.times.map(timeLabel),
      axisLine: { lineStyle: { color: '#b7c2cc' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#647383',
        fontSize: 8,
        interval: Math.max(0, Math.ceil(props.times.length / 10) - 1),
      },
      splitArea: { show: false },
    },
    yAxis: {
      type: 'category',
      data: props.rows.map((row) => `AS${row.asn}`),
      inverse: true,
      axisLine: { lineStyle: { color: '#b7c2cc' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#536371',
        fontFamily: 'ui-monospace,SFMono-Regular,Menlo,monospace',
        fontSize: 8,
      },
    },
    visualMap: {
      show: false,
      type: 'piecewise',
      dimension: 2,
      pieces: [
        { value: -1, color: '#e6e9ec' },
        { value: 0, color: '#167c68' },
        { value: 1, color: '#e09532' },
        { value: 2, color: '#8c3f58' },
      ],
    },
    series: [{
      name: 'ASN 可见状态',
      type: 'heatmap',
      data: cells.value,
      progressive: 3_000,
      emphasis: {
        itemStyle: {
          borderColor: '#172632',
          borderWidth: 1,
        },
      },
    }],
  }, true)
}

watch(() => [props.rows, props.times], renderChart, { deep: true })

onMounted(() => {
  renderChart()
  if (chartElement.value) {
    resizeObserver = new ResizeObserver(() => chart?.resize())
    resizeObserver.observe(chartElement.value)
  }
})

onBeforeUnmount(() => {
  resizeObserver?.disconnect()
  chart?.dispose()
})
</script>

<template>
  <div class="heatmap-shell">
    <div
      ref="chartElement"
      class="heatmap-canvas"
      :style="{ height: `${height}px` }"
      role="img"
      :aria-label="`ASN 与时间状态点热力图，当前显示 ${rows.length} 个 ASN`"
    ></div>
    <p v-if="rows.length === 0" class="heatmap-empty">当前筛选没有 ASN</p>
  </div>
</template>

<style scoped>
.heatmap-shell {
  position: relative;
  min-width: 0;
}

.heatmap-canvas {
  width: 100%;
  min-width: 0;
}

.heatmap-empty {
  position: absolute;
  inset: 50% auto auto 50%;
  margin: 0;
  color: #71808d;
  font-size: 11px;
  transform: translate(-50%, -50%);
}
</style>
