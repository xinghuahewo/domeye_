<template>
  <div class="resource-chart-wrapper" v-show="shouldShowChart">
    <div class="chart-toolbar">
      <span class="toolbar-label">Y轴范围</span>
      <el-button-group>
        <el-button
          size="small"
          :type="useRelativeYAxis ? 'default' : 'primary'"
          @click="setYAxisMode(false)"
        >
          从0开始
        </el-button>
        <el-button
          size="small"
          :type="useRelativeYAxis ? 'primary' : 'default'"
          @click="setYAxisMode(true)"
        >
          按最大最小
        </el-button>
      </el-button-group>
    </div>
    <div 
      ref="chartContainer" 
      class="chart-container"
    ></div>
  </div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';

interface ResourceData {
  time?: string;
  t?: string;
  v4Prefix_num: number;
  v6Prefix_num: number;
  v4IP_num: number;
}

interface Props {
  data: ResourceData[];
  title: string;
  loading: boolean;
}

const props = withDefaults(defineProps<Props>(), {
  data: () => [],
  title: '',
  loading: false
});

const chartContainer = ref<HTMLElement | null>(null);
let chartInstance: echarts.ECharts | null = null;
let resizeObserver: ResizeObserver | null = null;

// 当前显示的指标
const currentVisibleMetric = ref<'IPv4 前缀数量' | 'IPv6 前缀数量' | 'IPv4 地址数量'>('IPv4 地址数量');
const useRelativeYAxis = ref(false);

// 计算属性：判断是否应该显示图表
const shouldShowChart = computed(() => {
  return !props.loading && props.data.length > 0;
});

// 获取Y轴标签
const getYAxisLabel = () => {
  return currentVisibleMetric.value;
};

// 获取当前显示的数据
const getCurrentData = (data: ResourceData[]) => {
  switch (currentVisibleMetric.value) {
    case 'IPv4 地址数量':
      return data.map(item => item.v4IP_num || 0);
    case 'IPv4 前缀数量':
      return data.map(item => item.v4Prefix_num || 0);
    case 'IPv6 前缀数量':
      return data.map(item => item.v6Prefix_num || 0);
    default:
      return data.map(item => item.v4IP_num || 0);
  }
};

const getYAxisBounds = (values: number[]) => {
  const validData = values.filter((val) => Number.isFinite(val) && val >= 0);
  if (!validData.length) {
    return { min: 0, max: 100, interval: 20 };
  }

  if (!useRelativeYAxis.value) {
    const dataMax = Math.max(...validData);
    const axisMax = Math.max(1, Math.ceil(dataMax * 1.1));
    return {
      min: 0,
      max: axisMax,
      interval: Math.max(1, Math.ceil(axisMax / 5)),
    };
  }

  const dataMin = Math.min(...validData);
  const dataMax = Math.max(...validData);

  if (dataMin === dataMax) {
    const padding = Math.max(1, Math.ceil(dataMax * 0.05));
    const min = Math.max(0, dataMin - padding);
    const max = dataMax + padding;
    return {
      min,
      max,
      interval: Math.max(1, Math.ceil((max - min) / 5)),
    };
  }

  const padding = Math.max(1, Math.ceil((dataMax - dataMin) * 0.1));
  const min = Math.max(0, dataMin - padding);
  const max = dataMax + padding;

  return {
    min,
    max,
    interval: Math.max(1, Math.ceil((max - min) / 5)),
  };
};

const setYAxisMode = (relative: boolean) => {
  if (useRelativeYAxis.value === relative) return;
  useRelativeYAxis.value = relative;
  if (chartInstance && props.data.length > 0) {
    updateChart(props.data, props.title);
  }
};

// 初始化图表
const initChart = () => {
  if (chartContainer.value && !chartInstance) {
    chartInstance = echarts.init(chartContainer.value);
    console.log('Resource图表实例初始化成功');
  }
};

// 更新图表
const updateChart = (data: ResourceData[], title: string) => {
  if (!chartInstance) {
    console.error('图表实例未初始化，尝试重新初始化...');
    initChart();
    if (!chartInstance) {
      console.error('图表实例初始化失败');
      return;
    }
  }

  console.log('更新Resource图表数据:', data);
  
  // 兼容不同的时间字段名
  const timeData = data.map(item => item.time || item.t);
  
  // 检查数据有效性
  if (timeData.length === 0 || timeData.every(t => !t)) {
    console.warn('时间数据为空');
    ElMessage.warning('数据格式异常：缺少时间信息');
    return;
  }

  // 获取当前显示的数据
  const currentData = getCurrentData(data);
  const yAxisBounds = getYAxisBounds(currentData);

  const option: echarts.EChartsOption = {
    tooltip: {
      trigger: 'axis',
      axisPointer: {
        animation: false,
      },
      formatter: function(params: any) {
        if (!params || params.length === 0) return '';
        const time = echarts.format.formatTime('yyyy-MM-dd hh:mm:ss', params[0].axisValue);
        let tooltip = `${time}<br/>`;
        params.forEach((param: any) => {
          const value = param.value[1];
          const formattedValue = value.toLocaleString();
          tooltip += `${param.marker}${param.seriesName}: ${formattedValue}<br/>`;
        });
        return tooltip;
      }
    },
    legend: {
      data: ['IPv4 前缀数量', 'IPv6 前缀数量', 'IPv4 地址数量'],
      bottom: '2%',
      textStyle: {
        fontSize: 12
      },
      selected: {
        'IPv4 前缀数量': currentVisibleMetric.value === 'IPv4 前缀数量',
        'IPv6 前缀数量': currentVisibleMetric.value === 'IPv6 前缀数量',
        'IPv4 地址数量': currentVisibleMetric.value === 'IPv4 地址数量'
      }
    },
    grid: {
      left: '3%',
      right: '4%',
      bottom: '12%',
      top: '15%',
      containLabel: true,
    },
    xAxis: {
      type: 'time',
      axisLabel: {
        formatter: (value: number) => echarts.format.formatTime('MM-dd\nhh:mm', value),
      },
    },
    yAxis: {
      type: 'value',
      name: getYAxisLabel(),
      position: 'left',
      min: yAxisBounds.min,
      max: yAxisBounds.max,
      interval: yAxisBounds.interval,
      axisLabel: {
        formatter: (value: number) => Math.round(value).toLocaleString(),
      },
      splitLine: {
        show: true,
      },
    },
    series: [
      {
        name: 'IPv4 前缀数量',
        type: 'line',
        data: currentVisibleMetric.value === 'IPv4 前缀数量' ? 
          data.map((item, i) => [timeData[i], item.v4Prefix_num || 0]) : [],
        showSymbol: false,
        smooth: false,
        step: 'end',
        lineStyle: {
          color: '#1f77b4',
          width: 2
        },
        itemStyle: {
          color: '#1f77b4',
        },
      },
      {
        name: 'IPv6 前缀数量',
        type: 'line',
        data: currentVisibleMetric.value === 'IPv6 前缀数量' ? 
          data.map((item, i) => [timeData[i], item.v6Prefix_num || 0]) : [],
        showSymbol: false,
        smooth: false,
        step: 'end',
        lineStyle: {
          color: '#ff7f0e',
          width: 2
        },
        itemStyle: {
          color: '#ff7f0e',
        },
      },
      {
        name: 'IPv4 地址数量',
        type: 'line',
        data: currentVisibleMetric.value === 'IPv4 地址数量' ? 
          data.map((item, i) => [timeData[i], item.v4IP_num || 0]) : [],
        showSymbol: false,
        smooth: false,
        step: 'end',
        lineStyle: {
          color: '#2ca02c',
          width: 2
        },
        itemStyle: {
          color: '#2ca02c',
        },
      },
    ],
  };

  try {
    chartInstance.setOption(option);
    console.log('Resource图表更新成功');
    
    // 监听图例选择事件
    chartInstance.off('legendselectchanged');
    chartInstance.on('legendselectchanged', (params: any) => {
      // 获取当前点击的图例名称
      const clickedLegend = params.name as 'IPv4 前缀数量' | 'IPv6 前缀数量' | 'IPv4 地址数量';
      
      // 如果点击的不是当前显示的指标，则切换
      if (clickedLegend !== currentVisibleMetric.value) {
        currentVisibleMetric.value = clickedLegend;
        // 重新更新图表
        updateChart(data, title);
      }
    });
    
    // 强制重新渲染图表
    nextTick(() => {
      chartInstance?.resize();
    });
  } catch (error) {
    console.error('Resource图表更新失败:', error);
  }
};

// 监听数据变化
watch(
  () => [props.data, props.title] as const,
  ([newData, newTitle]) => {
    if (newData.length > 0) {
      nextTick(() => {
        updateChart(newData, newTitle);
      });
    }
  },
  { immediate: true, deep: true }
);

// 监听窗口大小变化
const handleResize = () => {
  if (chartInstance) {
    chartInstance.resize();
  }
};

onMounted(() => {
  nextTick(() => {
    initChart();
    if (chartContainer.value && typeof ResizeObserver !== 'undefined') {
      resizeObserver = new ResizeObserver(() => {
        handleResize();
      });
      resizeObserver.observe(chartContainer.value);
    }
    window.addEventListener('resize', handleResize);
  });
});

onUnmounted(() => {
  if (chartInstance) {
    chartInstance.dispose();
    chartInstance = null;
  }
  if (resizeObserver) {
    resizeObserver.disconnect();
    resizeObserver = null;
  }
  window.removeEventListener('resize', handleResize);
});

defineExpose({
  chartInstance,
  exportChart: () => {
    if (!chartInstance) {
      ElMessage.error('图表未初始化');
      return null;
    }
    return chartInstance.getDataURL({
      type: 'png',
      pixelRatio: 2,
      backgroundColor: '#fff'
    });
  }
});
</script>

<style scoped>
.resource-chart-wrapper {
  width: 100%;
  height: 100%;
  min-height: 500px;
  display: flex;
  flex-direction: column;
}

.chart-toolbar {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 12px;
  padding: 12px 16px 0;
  flex-shrink: 0;
}

.toolbar-label {
  font-size: 13px;
  color: #606266;
}

.chart-container {
  width: 100%;
  flex: 1;
  min-height: 0;
}
</style>
