<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { CORE_EVENT_TYPES, type EventLabel, type EventTrendPoint } from '@/types/api'

const props = withDefaults(defineProps<{
  points: EventTrendPoint[]
  height?: number
}>(), {
  height: 300,
})

const emit = defineEmits<{
  select: [eventType: EventLabel]
}>()

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const colors: Record<EventLabel, string> = {
  前缀劫持: '#0b57b7',
  子前缀劫持: '#537fc8',
  前缀中断: '#35b6d4',
  AS中断: '#16875d',
  国家中断: '#7a8699',
  路由泄漏: '#f48120',
}

const hasData = computed(() => props.points.some((point) => point.total > 0))

function renderChart() {
  if (!chartElement.value) return
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.setOption({
    animationDuration: 360,
    animationEasing: 'cubicOut',
    color: CORE_EVENT_TYPES.map((eventType) => colors[eventType]),
    grid: { left: 42, right: 16, top: 62, bottom: 42 },
    legend: {
      type: 'scroll',
      top: 5,
      left: 8,
      right: 8,
      itemWidth: 11,
      itemHeight: 7,
      textStyle: { color: '#667085', fontFamily: 'Avenir Next, PingFang SC, sans-serif', fontSize: 9 },
    },
    tooltip: {
      trigger: 'axis',
      axisPointer: { type: 'shadow' },
      borderWidth: 1,
      borderColor: '#d0d5dd',
      backgroundColor: '#ffffff',
      extraCssText: 'box-shadow:0 8px 24px rgba(31,41,51,.10);border-radius:6px;',
      textStyle: { color: '#1f2933', fontSize: 11 },
      valueFormatter: (value: unknown) => `${value ?? '—'} 起`,
    },
    xAxis: {
      type: 'time',
      axisLine: { lineStyle: { color: '#d0d5dd' } },
      axisTick: { show: false },
      axisLabel: { color: '#667085', hideOverlap: true, fontSize: 9 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      name: '起',
      nameTextStyle: { color: '#667085', fontSize: 9 },
      axisLabel: { color: '#667085', fontSize: 9 },
      splitLine: { lineStyle: { color: '#e8edf2', type: 'dashed' } },
    },
    series: CORE_EVENT_TYPES.map((eventType) => ({
      name: eventType,
      type: 'bar',
      stack: 'events',
      barMaxWidth: 22,
      emphasis: { focus: 'series' },
      data: props.points.map((point) => [point.time, point.counts[eventType]]),
    })),
  }, true)
}

watch(() => props.points, renderChart, { deep: true })

onMounted(() => {
  renderChart()
  chart?.on('click', (params) => {
    if (typeof params.seriesName === 'string' && CORE_EVENT_TYPES.includes(params.seriesName as EventLabel)) {
      emit('select', params.seriesName as EventLabel)
    }
  })
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
  <div class="event-chart-shell" :style="{ minHeight: `${height}px` }">
    <div ref="chartElement" class="event-chart-canvas" :style="{ height: `${height}px` }"></div>
    <p v-if="!hasData" class="chart-empty">当前窗口没有六类核心异常</p>
  </div>
</template>

<style scoped>
.event-chart-shell {
  position: relative;
  width: 100%;
  overflow: hidden;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: 6px;
}

.event-chart-canvas {
  width: 100%;
}

.chart-empty {
  position: absolute;
  inset: 50% auto auto 50%;
  margin: 0;
  transform: translate(-50%, -50%);
  color: var(--muted);
  font-size: 11px;
}
</style>
