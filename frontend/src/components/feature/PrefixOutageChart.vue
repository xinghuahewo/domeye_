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

interface PrefixOutageData {
  time_slot: string;
  outage_count: number;
}

interface Props {
  data: PrefixOutageData[];
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
    console.log('Prefix中断图表实例初始化成功');
  }
};

// 更新图表
const updateChart = (data: PrefixOutageData[], title: string) => {
  if (!chartInstance) {
    console.error('图表实例未初始化，尝试重新初始化...');
    initChart();
    if (!chartInstance) {
      console.error('图表实例初始化失败');
      return;
    }
  }

  console.log('更新Prefix中断图表数据:', data);
  
  const timeData = data.map(item => item.time_slot);
  const outageData = data.map(item => item.outage_count);

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
      data: ['Prefix中断数量'],
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
      name: 'Prefix中断数量',
      axisLabel: {
        formatter: '{value}',
      },
      splitLine: {
        show: true,
      },
    },
    series: [
      {
        name: 'Prefix中断数量',
        type: 'line',
        data: outageData.map((val, i) => [timeData[i], val]),
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
        areaStyle: {
          color: 'rgba(44, 160, 44, 0.1)'
        }
      }
    ],
  };

  try {
    chartInstance.setOption(option);
    console.log('Prefix中断图表更新成功');
    
    // 强制重新渲染图表
    nextTick(() => {
      chartInstance?.resize();
    });
  } catch (error) {
    console.error('Prefix中断图表更新失败:', error);
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
