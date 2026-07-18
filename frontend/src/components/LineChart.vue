<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface ChartSeries {
  name: string
  color: string
  data: Array<[string, number | null]>
}

const props = withDefaults(defineProps<{
  series: ChartSeries[]
  unit?: string
  height?: number
}>(), {
  unit: '',
  height: 300,
})

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const hasData = computed(() => props.series.some((series) => series.data.length > 0))

function renderChart() {
  if (!chartElement.value) return
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.setOption({
    animationDuration: 360,
    animationEasing: 'cubicOut',
    color: props.series.map((item) => item.color),
    grid: { left: 50, right: 18, top: 42, bottom: 44 },
    legend: {
      top: 4,
      right: 12,
      itemWidth: 18,
      itemHeight: 2,
      textStyle: { color: '#667085', fontFamily: 'Avenir Next, PingFang SC, sans-serif', fontSize: 10 },
    },
    tooltip: {
      trigger: 'axis',
      borderWidth: 1,
      borderColor: '#d0d5dd',
      backgroundColor: '#ffffff',
      extraCssText: 'box-shadow:0 8px 24px rgba(31,41,51,.10);border-radius:6px;',
      textStyle: { color: '#1f2933', fontSize: 11 },
      axisPointer: { type: 'line', lineStyle: { color: '#98a2b3', type: 'dashed' } },
      valueFormatter: (value: unknown) => `${value ?? '—'}${props.unit}`,
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#d0d5dd' } },
      axisTick: { show: false },
      axisLabel: { color: '#667085', hideOverlap: true, fontSize: 9 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      name: props.unit,
      nameTextStyle: { color: '#667085', fontSize: 9 },
      axisLabel: { color: '#667085', fontSize: 9 },
      splitLine: { lineStyle: { color: '#e8edf2', type: 'dashed' } },
    },
    series: props.series.map((item) => ({
      name: item.name,
      type: 'line',
      data: item.data,
      showSymbol: false,
      connectNulls: false,
      smooth: 0.12,
      lineStyle: { width: 2.2 },
      emphasis: { focus: 'series' },
    })),
  }, true)
}

watch(() => [props.series, props.unit], renderChart, { deep: true })

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
  <div class="chart-shell" :style="{ minHeight: `${height}px` }">
    <div ref="chartElement" class="chart-canvas" :style="{ height: `${height}px` }"></div>
    <p v-if="!hasData" class="chart-empty">当前范围没有可绘制数据</p>
  </div>
</template>
