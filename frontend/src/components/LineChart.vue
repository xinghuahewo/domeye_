<script setup lang="ts">
import * as echarts from 'echarts'
import { computed, onBeforeUnmount, onMounted, ref, watch } from 'vue'

import { formatChartTime, type TimeInput } from '@/utils/chartTime'

export interface ChartSeries {
  name: string
  color: string
  data: Array<[string, number | null]>
}

export interface ChartMarker {
  time: string
  label: string
  color?: string
}

const props = withDefaults(defineProps<{
  series: ChartSeries[]
  markers?: ChartMarker[]
  unit?: string
  height?: number
  timezone?: string
  showDataZoom?: boolean
}>(), {
  unit: '',
  height: 300,
  markers: () => [],
  timezone: 'Asia/Shanghai',
  showDataZoom: false,
})

const chartElement = ref<HTMLDivElement | null>(null)
let chart: echarts.ECharts | null = null
let resizeObserver: ResizeObserver | null = null

const hasData = computed(() => props.series.some((series) => series.data.length > 0))

function escapeHtml(value: unknown) {
  return String(value)
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#039;')
}

interface TooltipItem {
  axisValue?: TimeInput
  color?: string
  seriesName?: string
  data?: [string, number | null]
  value?: [string, number | null]
}

function tooltipContent(parameters: unknown) {
  const items = (Array.isArray(parameters) ? parameters : [parameters]) as TooltipItem[]
  const first = items[0]
  if (!first) return ''
  const lines = items.map((item) => {
    const tuple = Array.isArray(item.data) ? item.data : item.value
    const value = tuple?.[1]
    const rendered = typeof value === 'number'
      ? `${value.toLocaleString('zh-CN')}${props.unit}`
      : '—（缺失）'
    return `<div style="display:flex;align-items:center;justify-content:space-between;gap:18px;margin-top:6px"><span><i style="display:inline-block;width:7px;height:7px;margin-right:7px;border-radius:50%;background:${escapeHtml(item.color || '#667085')}"></i>${escapeHtml(item.seriesName || '')}</span><b style="font-family:ui-monospace,SFMono-Regular,Menlo,monospace">${escapeHtml(rendered)}</b></div>`
  })
  return `<div style="min-width:190px"><div style="color:#667085;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:10px">${escapeHtml(formatChartTime(first.axisValue ?? '', props.timezone, true))} · ${escapeHtml(props.timezone)}</div>${lines.join('')}</div>`
}

function renderChart() {
  if (!chartElement.value) return
  chart ||= echarts.init(chartElement.value, undefined, { renderer: 'canvas' })
  const largestSeries = Math.max(0, ...props.series.map((item) => item.data.length))
  const largeDataset = largestSeries >= 5_000
  chart.setOption({
    animation: !largeDataset,
    animationDuration: largeDataset ? 0 : 360,
    animationEasing: 'cubicOut',
    color: props.series.map((item) => item.color),
    grid: { left: 58, right: 18, top: 42, bottom: props.showDataZoom ? 76 : 44 },
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
      formatter: tooltipContent,
    },
    xAxis: {
      type: 'time',
      boundaryGap: false,
      axisLine: { lineStyle: { color: '#d0d5dd' } },
      axisTick: { show: false },
      axisLabel: {
        color: '#667085',
        hideOverlap: true,
        fontSize: 9,
        formatter: (value: number) => formatChartTime(value, props.timezone),
      },
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
    dataZoom: props.showDataZoom ? [
      {
        type: 'inside',
        filterMode: 'none',
        zoomOnMouseWheel: true,
        moveOnMouseMove: true,
      },
      {
        type: 'slider',
        filterMode: 'none',
        height: 18,
        bottom: 16,
        borderColor: '#d0d5dd',
        backgroundColor: '#f8fafc',
        fillerColor: 'rgba(11, 87, 183, .12)',
        dataBackground: {
          lineStyle: { color: '#98a2b3', width: 1 },
          areaStyle: { color: 'rgba(152, 162, 179, .08)' },
        },
        selectedDataBackground: {
          lineStyle: { color: '#0b57b7', width: 1 },
          areaStyle: { color: 'rgba(11, 87, 183, .08)' },
        },
        textStyle: { color: '#667085', fontSize: 8 },
      },
    ] : [],
    series: props.series.map((item, index) => ({
      name: item.name,
      type: 'line',
      data: item.data,
      showSymbol: false,
      connectNulls: false,
      smooth: false,
      progressive: 2_000,
      progressiveThreshold: 5_000,
      lineStyle: { width: 2.2 },
      emphasis: { focus: 'series' },
      ...(index === 0 && props.markers.length > 0 ? {
        markLine: {
          silent: true,
          symbol: ['none', 'none'],
          animation: false,
          lineStyle: { color: '#f48120', width: 1, type: 'dashed', opacity: 0.7 },
          label: {
            show: true,
            position: 'insideEndTop',
            color: '#b54708',
            fontSize: 8,
            formatter: (params: { data?: { name?: string } }) => params.data?.name || '',
          },
          data: props.markers.map((marker) => ({
            name: marker.label,
            xAxis: marker.time,
            lineStyle: { color: marker.color || '#f48120' },
          })),
        },
      } : {}),
    })),
  }, true)
}

watch(
  () => [props.series, props.markers, props.unit, props.timezone, props.showDataZoom],
  renderChart,
  { deep: true },
)

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
