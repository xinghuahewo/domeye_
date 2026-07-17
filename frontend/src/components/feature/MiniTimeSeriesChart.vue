<template>
    <div 
      ref="chartContainer" 
      class="mini-chart-container"
      :style="{ width: width, height: height }"
    ></div>
  </template>
  
  <script setup lang="ts">
  import { ref, onMounted, onUnmounted, watch, nextTick } from 'vue';
  import * as echarts from 'echarts';
  
  // 定义Props接口
  interface Props {
    data: Array<{ time: string; value?: number; announce?: number; withdraw?: number; }>;
    width?: string;
    height?: string;
    color?: string;
    showTooltip?: boolean;
    displayType?: 'total' | 'announce' | 'withdraw'; // 显示类型
    smooth?: boolean;
    showArea?: boolean;
    showDualLines?: boolean; // 是否显示双线模式
  }
  
  // 定义Props
  const props = withDefaults(defineProps<Props>(), {
    width: '120px',
    height: '100px',
    color: '#1890ff',
    showTooltip: true,
    displayType: 'total',
    smooth: true,
    showArea: true,
    showDualLines: false
  });
  
  // 图表容器引用
  const chartContainer = ref<HTMLElement | null>(null);
  let chartInstance: echarts.ECharts | null = null;
  
  // 初始化图表
  const initChart = () => {
    if (chartContainer.value && !chartInstance) {
      chartInstance = echarts.init(chartContainer.value);
      console.log('微型图表初始化成功');
      updateChart();
    }
  };
  
  // 数据处理函数
  const processData = () => {
    if (!props.data?.length) return { timeData: [], valueData: [] };
  
    const timeData = props.data.map(item => item.time);
    let valueData: number[] = [];
  
    // 根据displayType处理数据
    switch (props.displayType) {
      case 'announce':
        valueData = props.data.map(item => item.announce || 0);
        break;
      case 'withdraw':
        valueData = props.data.map(item => item.withdraw || 0);
        break;
      case 'total':
      default:
        valueData = props.data.map(item => {
          // 如果有value字段直接使用，否则计算announce + withdraw
          if (typeof item.value === 'number') {
            return item.value;
          }
          return (item.announce || 0) + (item.withdraw || 0);
        });
        break;
    }
  
    return { timeData, valueData };
  };
  
  // 更新图表数据
  const updateChart = () => {
    if (!chartInstance) return;
  
    const { timeData, valueData } = processData();
    
    if (!timeData.length) {
      // 清空图表
      chartInstance.clear();
      return;
    }

    let series: any[] = [];
    
    if (props.showDualLines && props.data?.length) {
      // 双线模式：显示回撤报文和宣告报文两条线
      const withdrawData = props.data.map(item => item.withdraw || 0);
      const announceData = props.data.map(item => item.announce || 0);
      
      series = [
        {
          name: '回撤报文',
          data: withdrawData,
          type: 'line',
          smooth: props.smooth,
          symbol: 'none',
          lineStyle: {
            color: '#1f77b4', // 蓝色
            width: 2
          },
          // 双线模式下不使用面积填充，避免重叠
        },
        {
          name: '宣告报文',
          data: announceData,
          type: 'line',
          smooth: props.smooth,
          symbol: 'none',
          lineStyle: {
            color: '#d62728', // 红色  
            width: 2
          },
          // 双线模式下不使用面积填充，避免重叠
        }
      ];
    } else {
      // 单线模式：根据displayType显示一条线
      series = [
        {
          data: valueData,
          type: 'line',
          smooth: props.smooth,
          symbol: 'none',
          lineStyle: {
            color: props.color,
            width: 2
          },
          areaStyle: props.showArea ? {
            color: {
              type: 'linear',
              x: 0,
              y: 0,
              x2: 0,
              y2: 1,
              colorStops: [
                { offset: 0, color: props.color + '40' },
                { offset: 1, color: props.color + '10' }
              ]
            }
          } : undefined
        }
      ];
    }
  
    const option: echarts.EChartsOption = {
      grid: {
        left: '2%',
        right: '2%',
        top: '5%',
        bottom: '5%',
        containLabel: false
      },
      tooltip: props.showTooltip ? {
        trigger: 'axis',
        confine: true,
        formatter: function(params: any) {
          if (!params || params.length === 0) return '';
          
          if (props.showDualLines) {
            let tooltip = `${params[0].name}<br/>`;
            if (params[0]) tooltip += `回撤报文: ${params[0].value}<br/>`;
            if (params[1]) tooltip += `宣告报文: ${params[1].value}`;
            return tooltip;
          } else {
            const param = params[0];
            let displayName = '报文数量';
            switch (props.displayType) {
              case 'announce':
                displayName = '宣告报文';
                break;
              case 'withdraw':
                displayName = '回撤报文';
                break;
              case 'total':
                displayName = '总报文数';
                break;
            }
            return `${param.name}<br/>${displayName}: ${param.value}`;
          }
        }
      } : undefined,
      xAxis: {
        type: 'category',
        data: timeData,
        show: false
      },
      yAxis: {
        type: 'value',
        show: false
      },
      series: series
    };
  
    chartInstance.setOption(option, true);
    
    // 强制重新渲染
    setTimeout(() => {
      if (chartInstance) {
        chartInstance.resize();
      }
    }, 50);
  };
  
  // 监听数据变化
  watch(() => [props.data, props.displayType, props.color, props.showDualLines], () => {
    nextTick(() => {
      if (chartInstance) {
        updateChart();
      } else {
        initChart();
      }
    });
  }, { deep: true });
  
  // 监听尺寸变化
  watch(() => [props.width, props.height], () => {
    nextTick(() => {
      if (chartInstance) {
        chartInstance.resize();
      }
    });
  });
  
  // 组件挂载时初始化
  onMounted(() => {
    nextTick(() => {
      initChart();
    });
  });
  
  // 组件卸载时销毁图表
  onUnmounted(() => {
    if (chartInstance) {
      chartInstance.dispose();
      chartInstance = null;
    }
  });
  </script>
  
  <style scoped>
  .mini-chart-container {
    display: block;
    position: relative;
    width: 100%;
    height: 100%;
    min-height: 80px;
  }
  
  .mini-chart-container:hover {
    z-index: 1000;
  }
  </style>