<template>
  <div style="width: 100%;height: 100%">
    <el-icon :color="'#fff'" :size="25" style="position: absolute;top: 15px;right: 10px;z-index: 10" @click="SwitchBigSrceen"><FullScreen /></el-icon>
    <div id="BoundaryCharts" class="BoundaryCharts"></div>
  </div>
</template>

<script setup lang="ts" name="BoundaryCharts">
import * as echarts from "echarts";
import {onMounted, reactive} from "vue";
import {NextLoading} from "/@/utils/loading";
import {FullScreen} from "@element-plus/icons-vue";
import BoundaryData from '/@/assets/Boundary/boundary.json'

let myChart = null

let CenterTopData = reactive({
  nodes: [],
  links: [],
  categories: [],
  pie_data: []
})

const initBoundaryCharts = () => {
  CenterTopData = BoundaryData.filter((item) => item.visualCountry === '中国')[0].peerArea.filter((item) => item.countryArea === 'G20')[0].peerData.CenterTopData

  const chartDom = document.getElementById('BoundaryCharts');
  myChart = echarts.init(chartDom);
  CenterTopData.nodes.forEach(function (node) {
    node.symbolSize = Math.pow(node.symbolSize, 1 / 3);
    node.label = {
      normal: {
        show: node.symbolSize >= 10,
      }
    };
  });

  let option = {
    tooltip: {},

    animationDuration: 1500,
    animationEasingUpdate: 'quinticInOut',
    series: [
      {
        name: '国家边界拓扑图',
        type: 'graph',
        layout: 'none',
        data: CenterTopData.nodes,
        links: CenterTopData.links,
        categories: CenterTopData.categories,
        roam: false,
        edgeSymbol: ['circle', 'arrow'],
        label: {
          normal: {
            show: false,
            position: 'right',
            formatter: '{b}'
          }
        },

        colorBy: 'data',

        lineStyle: {
          color: 'target',
          curveness: 0
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            width: 10
          }
        }
      },
      {
        name: 'Access From',
        type: 'pie',
        radius: ['88%', '93%'],
        startAngle: 0,
        colorBy: 'data',
        clockwise: false,
        avoidLabelOverlap: false,
        label: {
          show: true,
          position: 'outer'
        },
        labelLine: {
          show: false
        },
        data: CenterTopData.pie_data
      }
    ]
  };

  option && myChart.setOption(option);
}

const SwitchBigSrceen = () => {
  let full = document.getElementById("BoundaryCharts")
  if (full.requestFullscreen) {
    full.requestFullscreen()
  } else if (full.mozRequestFullScreen) {
    full.mozRequestFullScreen()
  } else if (full.webkitRequestFullscreen) {
    full.webkitRequestFullscreen()
  } else if (full.msRequestFullscreen) {
    full.msRequestFullscreen()
  }
}

onMounted(() => {
  NextLoading.done();
  initBoundaryCharts()
})
</script>

<style scoped lang="scss">
.BoundaryCharts{
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: url('../../../../assets/img.png');
  background-size: 100% 100%;
}
</style>