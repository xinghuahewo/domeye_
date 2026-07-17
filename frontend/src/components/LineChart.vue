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
    animationDuration: 450,
    color: props.series.map((item) => item.color),
    grid: { left: 52, right: 20, top: 42, bottom: 48 },
    legend: {
      top: 4,
      right: 12,
      itemWidth: 18,
      itemHeight: 2,
      textStyle: { color: '#59636c', fontFamily: 'ui-monospace, monospace', fontSize: 11 },
    },
    tooltip: {
      trigger: 'axis',
      borderWidth: 1,
      borderColor: '#111b24',
      backgroundColor: '#f8f6ef',
      textStyle: { color: '#111b24', fontSize: 12 },
      valueFormatter: (value: unknown) => `${value ?? '—'}${props.unit}`,
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#8f969b' } },
      axisTick: { show: false },
      axisLabel: { color: '#69737b', hideOverlap: true, fontSize: 10 },
      splitLine: { show: false },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      name: props.unit,
      nameTextStyle: { color: '#69737b', fontSize: 10 },
      axisLabel: { color: '#69737b', fontSize: 10 },
      splitLine: { lineStyle: { color: '#d7d5cd', type: 'dashed' } },
    },
    series: props.series.map((item) => ({
      name: item.name,
      type: 'line',
      data: item.data,
      showSymbol: false,
      connectNulls: false,
      lineStyle: { width: 2 },
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
