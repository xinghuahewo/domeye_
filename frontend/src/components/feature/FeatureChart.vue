<template>
  <div 
    ref="chartContainer" 
    class="chart-container"
    v-show="shouldShowChart"
  ></div>
</template>

<script setup lang="ts">
import { ref, onMounted, onUnmounted, nextTick, watch, computed } from 'vue';
import * as echarts from 'echarts';
import { ElMessage } from 'element-plus';

interface FeatureData {
  time?: string;
  t?: string;
  withdraw: number;
  announce: number;
}

interface Props {
  data: FeatureData[];
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

// 计算属性：判断是否应该显示图表
const shouldShowChart = computed(() => {
  return !props.loading && props.data.length > 0;
});

// 初始化图表
const initChart = () => {
  if (chartContainer.value && !chartInstance) {
    chartInstance = echarts.init(chartContainer.value);
    console.log('Feature图表实例初始化成功');
  }
};

// 更新图表
const updateChart = (data: FeatureData[], title: string) => {
  if (!chartInstance) {
    console.error('图表实例未初始化，尝试重新初始化...');
    initChart();
    if (!chartInstance) {
      console.error('图表实例初始化失败');
      return;
    }
  }

  console.log('更新Feature图表数据:', data);
  
  // 兼容不同的时间字段名
  const timeData = data.map(item => item.time || item.t);
  const withdrawData = data.map(item => item.withdraw || 0);
  const announceData = data.map(item => item.announce || 0);

  // 检查数据有效性
  if (timeData.length === 0 || timeData.every(t => !t)) {
    console.warn('时间数据为空');
    ElMessage.warning('数据格式异常：缺少时间信息');
    return;
  }

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
          tooltip += `${param.marker}${param.seriesName}: ${param.value[1]}<br/>`;
        });
        return tooltip;
      }
    },
    legend: {
      data: ['回撤报文', '宣告报文'],
      top: 'bottom',
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
      name: '报文数量',
      axisLabel: {
        formatter: '{value}',
      },
      splitLine: {
        show: true,
      },
    },
    series: [
      {
        name: '回撤报文',
        type: 'line',
        data: withdrawData.map((val, i) => [timeData[i], val]),
        showSymbol: false,
        smooth: false,
        lineStyle: {
          color: '#1f77b4',
          width: 2
        },
        itemStyle: {
          color: '#1f77b4',
        },
      },
      {
        name: '宣告报文',
        type: 'line',
        data: announceData.map((val, i) => [timeData[i], val]),
        showSymbol: false,
        smooth: false,
        lineStyle: {
          color: '#d62728',
          width: 2
        },
        itemStyle: {
          color: '#d62728',
        },
      },
    ],
  };

  try {
    chartInstance.setOption(option);
    console.log('Feature图表更新成功');
    
    // 强制重新渲染图表
    nextTick(() => {
      chartInstance?.resize();
    });
  } catch (error) {
    console.error('Feature图表更新失败:', error);
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
.chart-container {
  width: 100%;
  height: 100%;
  min-height: 500px;
}
</style> 
