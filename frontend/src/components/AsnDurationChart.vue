<script setup lang="ts">
import * as echarts from 'echarts'
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'

export interface DurationSeries {
  name: string
  color: string
  values: number[]
}

const props = withDefaults(defineProps<{
  categories: string[]
  series: DurationSeries[]
  height?: number
}>(), {
  height: 280,
})

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

function renderChart() {
  if (!chartElement.value) return
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  chart.setOption({
    animation: false,
    color: props.series.map((item) => item.color),
    grid: { left: 56, right: 18, top: 44, bottom: 54 },
    legend: {
      top: 5,
      right: 8,
      itemWidth: 10,
      itemHeight: 8,
      textStyle: { color: '#596979', fontSize: 9 },
    },
    tooltip: {
      trigger: 'axis',
      confine: true,
      axisPointer: { type: 'shadow' },
      borderWidth: 1,
      borderColor: '#cfd7df',
      backgroundColor: 'rgba(255,255,255,.98)',
      textStyle: { color: '#172632', fontSize: 11 },
      valueFormatter: (value: unknown) => `${Number(value).toLocaleString('zh-CN')} ASN`,
    },
    xAxis: {
      type: 'category',
      data: props.categories,
      name: '最长连续状态时间',
      nameLocation: 'middle',
      nameGap: 34,
      nameTextStyle: { color: '#647383', fontSize: 9 },
      axisLine: { lineStyle: { color: '#b7c2cc' } },
      axisTick: { show: false },
      axisLabel: { color: '#647383', fontSize: 8, interval: 0 },
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
      name: 'ASN 数量',
      nameTextStyle: { color: '#647383', fontSize: 9 },
      axisLabel: { color: '#647383', fontSize: 8 },
      splitLine: { lineStyle: { color: '#e2e8ed', type: 'dashed' } },
    },
    series: props.series.map((item) => ({
      name: item.name,
      type: 'bar',
      data: item.values,
      barMaxWidth: 14,
      itemStyle: { color: item.color },
      emphasis: { focus: 'series' },
    })),
  }, true)
}

watch(() => [props.categories, props.series], renderChart, { deep: true })

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
  <div
    ref="chartElement"
    class="duration-chart"
    :style="{ height: `${height}px` }"
    role="img"
    aria-label="ASN 可见状态最长连续时间分布图"
  ></div>
</template>

<style scoped>
.duration-chart {
  width: 100%;
  min-width: 0;
}
</style>
