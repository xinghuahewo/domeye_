<template>
  <div class="index" id="demo">
    <div class="topContainer">
      <div class="collapseContainer" v-show="!themeConfig.isFullScreen">
        <el-icon :color="'#fff'" :size="30" v-if="themeConfig.isCollapse" @click="ExpandOrFoldCollapse"><Expand /></el-icon>
        <el-icon :color="'#fff'" :size="30" v-else @click="ExpandOrFoldCollapse"><Fold /></el-icon>
      </div>
      <div class="titleContainer">国家边界关系态势感知平台</div>
      <div class="toolContainer">
        <span>观测地区：</span>
        <el-select-v2
            v-model="viewPlace"
            :options="viewPlaceOptions"
            placeholder="Please select"
            size="default"
            :clearable="false"
            style="width: 100px;margin-right: 10px"
        />
        <span>邻接地区：</span>
        <el-select-v2
            v-model="peerPlace"
            :options="peerPlaceOptions"
            placeholder="Please select"
            size="default"
            :clearable="false"
            style="width: 100px;margin-right: 10px"
        />
        <span>大屏页面：</span>
        <el-select-v2
            v-model="selectDemo"
            :options="demoOptions"
            placeholder="Please select"
            size="default"
            :clearable="false"
            style="width: 155px;margin-right: 10px"
        />
        <el-icon :color="'#fff'" :size="25" @click="SwitchBigSrceen"><FullScreen /></el-icon>
      </div>
    </div>

    <el-row class="bottomContainer" justify="space-around" align="middle">
      <el-col :span="6" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">出口边界统计概况</span>
          </div>
          <div class="mainContainer">
            <AsideTop :data="LeftTopData"></AsideTop>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': LeftCenterType === '排行' }" @click="chosenLeftCenterType('统计')">出口边界AS统计</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': LeftCenterType === '统计' }" @click="chosenLeftCenterType('排行')">出口边界AS(Top6)</span>
          </div>
          <div class="mainContainer">
            <div id="LeftCenterChart" style="width: 100%;height: 100%" v-show="LeftCenterType === '统计'"></div>
            <div style="width: 100%;height: 100%" v-show="LeftCenterType === '排行'">
              <el-row class="tableHeader" justify="space-between">
                <el-col :span="2">
                  排名
                </el-col>
                <el-col :span="6">
                  机构名称
                </el-col>
                <el-col :span="5">
                  AS名称
                </el-col>
                <el-col :span="5" @click="chosenLeftCenterRankType('边界')">
                  边界数量
                  <el-icon :color="'#0072ff'" :size="LeftCenterRankType === '边界' ? 16 : 12"><DCaret /></el-icon>
                </el-col>
                <el-col :span="5" @click="chosenLeftCenterRankType('路径')">
                  路径数量
                  <el-icon :color="'#0072ff'" :size="LeftCenterRankType === '路径' ? 16 : 12"><DCaret /></el-icon>
                </el-col>
              </el-row>
              <div style="width: 100%;height: calc(100% - 30px);">
                <el-row class="tableItem" v-for="(item, index) in LeftCenterRankType === '边界' ? LeftCenterData.rankData.boundaryRank : LeftCenterData.rankData.pathRank" :key="index" justify="space-between">
                  <el-col :span="2">
                    {{ index + 1 }}
                  </el-col>
                  <el-col :span="6">
                    {{ item.organization }}
                  </el-col>
                  <el-col :span="6">
                    {{ item.as }}
                  </el-col>
                  <el-col :span="4">
                    {{ item.boundaryNum }}
                  </el-col>
                  <el-col :span="5">
                    {{ item.pathNum }}
                  </el-col>
                </el-row>
              </div>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">出口下一跳路径(Top6)</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="LeftBottomType">
              <el-radio label="地区" size="default">地区</el-radio>
              <el-radio label="AS" size="default">AS</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="LeftBottomType === '地区' ? 4 : 2">
                排名
              </el-col>
              <el-col :span="LeftBottomType === '地区' ? 6 : 4">
                地区
              </el-col>
              <el-col :span="6" v-if="LeftBottomType !== '地区'">
                AS名称
              </el-col>
              <el-col :span="LeftBottomType === '地区' ? 6 : 5" @click="chosenLeftBottomRankType('边界')">
                边界数量
                <el-icon :color="'#00fff7'" :size="LeftBottomRankType === '边界' ? 18 : 12"><DCaret /></el-icon>
              </el-col>
              <el-col :span="LeftBottomType === '地区' ? 6 : 5" @click="chosenLeftBottomRankType('路径')">
                路径数量
                <el-icon :color="'#00fff7'" :size="LeftBottomRankType === '路径' ? 18 : 12"><DCaret /></el-icon>
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in LeftBottomType === '地区' ? LeftBottomType === '路径' ? LeftBottomData.country.pathRank : LeftBottomData.country.boundaryRank : LeftBottomRankType === '路径' ? LeftBottomData.as.pathRank : LeftBottomData.as.boundaryRank" :key="index" justify="space-between">
                <el-col :span="LeftBottomType === '地区' ? 4 : 2">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="LeftBottomType === '地区' ? 3 : 2">
                  <span class="flag-icon" :class="item.countryFlag"></span>
                </el-col>
                <el-col :span="LeftBottomType === '地区' ? 3 : 2">
                  {{ item.countryName }}
                </el-col>
                <el-col :span="6" v-if="LeftBottomType !== '地区'">
                  {{ item.as }}
                </el-col>
                <el-col :span="LeftBottomType === '地区' ? 6 : 5">
                  {{ item.boundaryNum }}
                </el-col>
                <el-col :span="LeftBottomType === '地区' ? 6 : 5">
                  {{ item.pathNum }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :span="11" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 66.5%;padding: 5px;position: relative">
          <div class="selectContainer">
            <el-icon :color="'#fff'" :size="25" @click="fullScreen"><View /></el-icon>
          </div>
          <div id="mainChart" style="width: 100%;height: 100%"></div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">边界联通关系态势</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="CenterBottomType">
              <el-radio label="新增" size="default">新增</el-radio>
              <el-radio label="断联" size="default">断联</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer" id="CenterBottomChart"></div>
        </div>
      </el-col>

      <el-col :span="6" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">入口边界统计概况</span>
          </div>
          <div class="mainContainer">
            <AsideTop :data="RightTopData"></AsideTop>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': RightCenterType === '排行' }" @click="chosenRightCenterType('统计')">入口下一跳AS统计</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': RightCenterType === '统计' }" @click="chosenRightCenterType('排行')">入口下一跳AS(Top6)</span>
          </div>
          <div class="mainContainer">
            <div id="RightCenterChart" style="width: 100%;height: 100%" v-show="RightCenterType === '统计'"></div>
            <div style="width: 100%;height: 100%" v-show="RightCenterType === '排行'">
              <el-row class="tableHeader" justify="space-between">
                <el-col :span="2">
                  排名
                </el-col>
                <el-col :span="6">
                  机构名称
                </el-col>
                <el-col :span="5">
                  AS名称
                </el-col>
                <el-col :span="5" @click="chosenRightCenterRankType('边界')">
                  边界数量
                  <el-icon :color="'#0072ff'" :size="RightCenterRankType === '边界' ? 16 : 12"><DCaret /></el-icon>
                </el-col>
                <el-col :span="5" @click="chosenRightCenterRankType('路径')">
                  路径数量
                  <el-icon :color="'#0072ff'" :size="RightCenterRankType === '路径' ? 16 : 12"><DCaret /></el-icon>
                </el-col>
              </el-row>
              <div style="width: 100%;height: calc(100% - 30px);">
                <el-row class="tableItem" v-for="(item, index) in RightCenterRankType === '边界' ? RightCenterData.rankData.boundaryRank : RightCenterData.rankData.pathRank" :key="index" justify="space-between">
                  <el-col :span="2">
                    {{ index + 1 }}
                  </el-col>
                  <el-col :span="6">
                    {{ item.organization }}
                  </el-col>
                  <el-col :span="6">
                    {{ item.as }}
                  </el-col>
                  <el-col :span="4">
                    {{ item.boundaryNum }}
                  </el-col>
                  <el-col :span="5">
                    {{ item.pathNum }}
                  </el-col>
                </el-row>
              </div>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">入口边界路径(Top6)</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="RightBottomType">
              <el-radio label="地区" size="default">地区</el-radio>
              <el-radio label="AS" size="default">AS</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="RightBottomType === '地区' ? 4 : 2">
                排名
              </el-col>
              <el-col :span="RightBottomType === '地区' ? 6 : 4">
                地区
              </el-col>
              <el-col :span="6" v-if="RightBottomType !== '地区'">
                AS名称
              </el-col>
              <el-col :span="RightBottomType === '地区' ? 6 : 5" @click="chosenRightBottomRankType('边界')">
                边界数量
                <el-icon :color="'#00fff7'" :size="RightBottomRankType === '边界' ? 18 : 12"><DCaret /></el-icon>
              </el-col>
              <el-col :span="RightBottomType === '地区' ? 6 : 5" @click="chosenRightBottomRankType('路径')">
                路径数量
                <el-icon :color="'#00fff7'" :size="RightBottomRankType === '路径' ? 18 : 12"><DCaret /></el-icon>
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in RightBottomType === '地区' ? RightBottomType === '路径' ? RightBottomData.country.pathRank : RightBottomData.country.boundaryRank : RightBottomRankType === '路径' ? RightBottomData.as.pathRank : RightBottomData.as.boundaryRank" :key="index" justify="space-between">
                <el-col :span="RightBottomType === '地区' ? 4 : 2">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="RightBottomType === '地区' ? 3 : 2">
                  <span class="flag-icon" :class="item.countryFlag"></span>
                </el-col>
                <el-col :span="RightBottomType === '地区' ? 3 : 2">
                  {{ item.countryName }}
                </el-col>
                <el-col :span="6" v-if="RightBottomType !== '地区'">
                  {{ item.as }}
                </el-col>
                <el-col :span="RightBottomType === '地区' ? 6 : 5">
                  {{ item.boundaryNum }}
                </el-col>
                <el-col :span="RightBottomType === '地区' ? 6 : 5">
                  {{ item.pathNum }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script setup lang="ts">
import {Expand, Fold, FullScreen, DCaret, View} from '@element-plus/icons-vue'
import {useThemeConfig} from "/@/stores/themeConfig";
import {storeToRefs} from "pinia";
import {onMounted, reactive, ref, watch} from "vue";
import screenfull from "screenfull";
import {ElMessage} from "element-plus";
import router from "/@/router";
import * as echarts from "echarts";
import AsideTop from '/@/newviews/visualization/Boundary/AsideTop/index1.vue'
import request from "/@/utils/request";


// 整体数据
const AllData = ref({})

const ShowData = ref({
  LeftTopData: [],
  LeftCenterData: {},
  LeftBottomData: {},
  CenterTopData: {},
  CenterBottomData: {},
  RightTopData: [],
  RightCenterData: {},
  RightBottomData: {}
})


// 选择大屏
const selectDemo = ref('国家边界关系')
const demoOptions = [
  {
    label: '国家边界关系',
    value: '国家边界关系',
  },
  {
    label: '国家联通关系',
    value: '国家联通关系',
  },
  {
    label: '互联网路由安全',
    value: '互联网路由安全',
  },
]

watch(selectDemo, (NewVal) => {
  if(NewVal === '国家边界关系')
    router.push('/visualization/boundary')
  else if(NewVal === '国家联通关系')
    router.push('/visualization/connectivity')
  else
    router.push('/visualization/safety')
})


// 左上
const LeftTopData = ref([])


// 左中
const LeftCenterType = ref('统计')

const chosenLeftCenterType = (val) => {
  LeftCenterType.value = val
}

const LeftCenterRankType = ref('路径')

const chosenLeftCenterRankType = (val) => {
  LeftCenterRankType.value = val
}

let LeftCenterData = reactive({
  chartData: {
    organization: [],
    as: []
  },
  rankData: {
    boundaryRank: [],
    pathRank: []
  },
})

let LeftCenterChart = null

const drawLeftCenterChart = () => {
  // 绘制图表
  if (LeftCenterChart != null && LeftCenterChart != "" && LeftCenterChart != undefined) {
    LeftCenterChart.dispose();//销毁
  }
  let chartDom = document.getElementById("LeftCenterChart")
  LeftCenterChart = echarts.init(chartDom)
  let option = {
    tooltip: {
      trigger: 'item',
      formatter: '{a} <br/>{b}: {c} ({d}%)'
    },
    series: [
      {
        name: 'Access From',
        type: 'pie',
        selectedMode: 'single',
        radius: [0, '50%'],
        label: {
          position: 'inner',
          fontSize: 12,
          color: '#fff',
          formatter: function (params) {
            if (params.name.length > 10) {
              params.name = params.name.substring(0, 10) + "..";
            }
            return params.name;
          }
        },
        labelLine: {
          show: false
        },
        data: LeftCenterData.chartData.organization
      },
      {
        name: 'Access From',
        type: 'pie',
        radius: ['75%', '85%'],
        labelLine: {
          length: 15
        },
        label: {
          formatter: '{b|{b}}',
          rich: {
            b: {
              color: '#fff',
              fontSize: 12,
              fontWeight: 'bold',
              lineHeight: 33
            }
          }
        },
        data: LeftCenterData.chartData.as
      }
    ]
  };
  option && LeftCenterChart.setOption(option);
}


// 左下
const LeftBottomType = ref('地区')

let LeftBottomData = reactive({
  country: {
    boundaryRank: [],
    pathRank: []
  },
  as: {
    boundaryRank: [],
    pathRank: []
  }
})

const LeftBottomRankType = ref('边界')

const chosenLeftBottomRankType = (val) => {
  LeftBottomRankType.value = val
}


// 中上
let CenterTopData = reactive({
  nodes: [],
  links: [],
  categories: [],
  pie_data: []
})

let CenterTopChart = null

const drawCenterTopChart = () => {
  // 绘制图表
  if (CenterTopChart != null && CenterTopChart != "" && CenterTopChart != undefined) {
    CenterTopChart.dispose();//销毁
  }
  let chartDom = document.getElementById("mainChart")
  CenterTopChart = echarts.init(chartDom)

  CenterTopData.nodes.forEach(function (node) {
    if(node.symbolSize > 30000)
      node.symbolSize = Math.pow(node.symbolSize, 1 / 3.8);
    else
      node.symbolSize = Math.pow(node.symbolSize, 1 / 3.4);
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

  option && CenterTopChart.setOption(option);
}

const fullScreen = () => {
  const href = router.resolve({
    path: '/BoundaryCharts',
  })
  window.open(href.href, '_blank')
}


// 中下
const CenterBottomType = ref('新增')

let CenterBottomData = reactive({
  TimeData: [],
  NewData: [],
  DisconnectionData: [],
  ErrorData: []
})

let CenterBottomChart = null

const drawCenterBottomChart = () => {
  if (CenterBottomChart != null && CenterBottomChart != "" && CenterBottomChart != undefined) {
    CenterBottomChart.dispose();//销毁
  }
  let chartDom = document.getElementById('CenterBottomChart');
  CenterBottomChart = echarts.init(chartDom);
  let option = {
    title: {
      text: CenterBottomType.value === '新增' ? '新增国家边界' : '断联国家边界',
      left: 'center',
      textStyle: {
        color: '#fff'
      }
    },
    tooltip: {
      trigger: 'item',
    },
    textStyle: {
      color: '#fff'
    },
    grid: {
      top: '15%',
      left: 0,
      right: 0,
      bottom: 0,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: CenterBottomData.TimeData
    },
    yAxis: {
      type: 'value',
      minInterval: 1,
    },
    series: [
      {
        data: CenterBottomType.value === '新增' ? CenterBottomData.NewData : CenterBottomData.DisconnectionData,
        type: 'line',
        itemStyle: {
          normal: {
            color: CenterBottomType.value === '新增' ? '#37ff00' : '#ff0000', //改变折线点的颜色
            lineStyle: {
              color: CenterBottomType.value === '新增' ? '#37ff00' : '#ff0000' //改变折线颜色
            }
          }
        },
        smooth: true
      }
    ]
  };
  option && CenterBottomChart.setOption(option);
}

watch(CenterBottomType, () => {
  drawCenterBottomChart()
})

// 右上
const RightTopData = ref([])

// 右中
const RightCenterType = ref('统计')

const chosenRightCenterType = (val) => {
  RightCenterType.value = val
}

const RightCenterRankType = ref('路径')

const chosenRightCenterRankType = (val) => {
  RightCenterRankType.value = val
}

let RightCenterData = reactive({
  chartData: {
    organization: [],
    as: []
  },
  rankData: {
    boundaryRank: [],
    pathRank: []
  },
})

let RightCenterChart = null

const drawRightCenterChart = () => {
  // 绘制图表
  if (RightCenterChart != null && RightCenterChart != "" && RightCenterChart != undefined) {
    RightCenterChart.dispose();//销毁
  }
  let chartDom = document.getElementById("RightCenterChart")
  RightCenterChart = echarts.init(chartDom)
  let option = {
    tooltip: {
      trigger: 'item',
      formatter: '{a} <br/>{b}: {c} ({d}%)'
    },
    series: [
      {
        name: 'Access From',
        type: 'pie',
        selectedMode: 'single',
        radius: [0, '50%'],
        label: {
          position: 'inner',
          fontSize: 12,
          color: '#fff',
          formatter: function (params) {
            if (params.name.length > 10) {
              params.name = params.name.substring(0, 10) + "..";
            }
            return params.name;
          }
        },
        labelLine: {
          show: false
        },
        data: RightCenterData.chartData.organization
      },
      {
        name: 'Access From',
        type: 'pie',
        radius: ['75%', '85%'],
        labelLine: {
          length: 15
        },
        label: {
          formatter: '{b|{b}}',
          rich: {
            b: {
              color: '#fff',
              fontSize: 12,
              fontWeight: 'bold',
              lineHeight: 33
            }
          }
        },
        data: RightCenterData.chartData.as
      }
    ]
  };
  option && RightCenterChart.setOption(option);
}


// 右下
const RightBottomType = ref('地区')

let RightBottomData = reactive({
  country: {
    boundaryRank: [],
    pathRank: []
  },
  as: {
    boundaryRank: [],
    pathRank: []
  }
})

const RightBottomRankType = ref('路径')

const chosenRightBottomRankType = (val) => {
  RightBottomRankType.value = val
}


// 设置折叠logo
const storesThemeConfig = useThemeConfig();
const { themeConfig } = storeToRefs(storesThemeConfig);


// 折叠切换
const ExpandOrFoldCollapse = () => {
  themeConfig.value.isCollapse = !themeConfig.value.isCollapse;
  setTimeout(() => {
    LeftCenterChart.resize()
    CenterTopChart.resize()
    CenterBottomChart.resize()
    RightCenterChart.resize()
  }, 200);
}


// 大屏切换
const SwitchBigSrceen = () => {
  if (!screenfull.isEnabled) {
    ElMessage.warning('暂不不支持全屏');
    return false;
  }
  screenfull.toggle();
  screenfull.on('change', () => {
    setTimeout(() => {
      LeftCenterChart.resize()
      CenterTopChart.resize()
      CenterBottomChart.resize()
      RightCenterChart.resize()
    }, 200);
    if (screenfull.isFullscreen)
      useThemeConfig().updateFullScreen(true)
    else
      useThemeConfig().updateFullScreen(false)
  });
}


// 选择出入口地区
const viewPlace = ref('')
const peerPlace = ref('')

const viewPlaceOptions = ref([])
const peerPlaceOptions = ref([])

watch(peerPlace, (NewVal) => {
  ShowData.value = AllData.value[NewVal]

  LeftTopData.value = ShowData.value.LeftTopData

  LeftCenterData = ShowData.value.LeftCenterData
  if(LeftCenterData.rankData.boundaryRank.length > 6)
    LeftCenterData.rankData.boundaryRank = LeftCenterData.rankData.boundaryRank.slice(0, 6)
  if(LeftCenterData.rankData.pathRank.length > 6)
    LeftCenterData.rankData.pathRank = LeftCenterData.rankData.pathRank.slice(0, 6)
  drawLeftCenterChart()

  LeftBottomData = ShowData.value.LeftBottomData
  if(LeftBottomData.country.boundaryRank.length > 6)
    LeftBottomData.country.boundaryRank = LeftBottomData.country.boundaryRank.slice(0, 6)
  if(LeftBottomData.country.pathRank.length > 6)
    LeftBottomData.country.pathRank = LeftBottomData.country.pathRank.slice(0, 6)
  if(LeftBottomData.as.boundaryRank.length > 6)
    LeftBottomData.as.boundaryRank = LeftBottomData.as.boundaryRank.slice(0, 6)
  if(LeftBottomData.as.pathRank.length > 6)
    LeftBottomData.as.pathRank = LeftBottomData.as.pathRank.slice(0, 6)

  CenterTopData = ShowData.value.CenterTopData
  drawCenterTopChart()

  CenterBottomData = ShowData.value.CenterBottomData
  CenterBottomData.DisconnectionData = CenterBottomData.DisconnectionData.map(item => Math.abs(item))
  drawCenterBottomChart()

  RightTopData.value = ShowData.value.RightTopData

  RightCenterData = ShowData.value.RightCenterData
  if(RightCenterData.rankData.boundaryRank.length > 6)
    RightCenterData.rankData.boundaryRank = RightCenterData.rankData.boundaryRank.slice(0, 6)
  if(RightCenterData.rankData.pathRank.length > 6)
    RightCenterData.rankData.pathRank = RightCenterData.rankData.pathRank.slice(0, 6)
  drawRightCenterChart()

  RightBottomData = ShowData.value.RightBottomData
  if(RightBottomData.country.boundaryRank.length > 6)
    RightBottomData.country.boundaryRank = RightBottomData.country.boundaryRank.slice(0, 6)
  if(RightBottomData.country.pathRank.length > 6)
    RightBottomData.country.pathRank = RightBottomData.country.pathRank.slice(0, 6)
  if(RightBottomData.as.boundaryRank.length > 6)
    RightBottomData.as.boundaryRank = RightBottomData.as.boundaryRank.slice(0, 6)
  if(RightBottomData.as.pathRank.length > 6)
    RightBottomData.as.pathRank = RightBottomData.as.pathRank.slice(0, 6)
})

const initShowData = async () => {
  const screenfileData = await request({
    url: 'geodata/boundaries/screenfile',
    method: 'get',
    data: {},
  });

  AllData.value = screenfileData

  console.log(AllData.value)

  // 设置可选项
  viewPlaceOptions.value = [
      {
        label: '中国',
        value: '中国'
      }
  ]
  viewPlace.value = viewPlaceOptions.value[0].value

  peerPlaceOptions.value = Object.keys(AllData.value).map((item) => {
    return {
      label: item,
      value: item
    }
  })
  peerPlace.value = peerPlaceOptions.value[0].value
}


onMounted(async () => {
  await initShowData()
})

</script>

<style scoped lang="scss">
.index{
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: url('../../../assets/img.png');
  background-size: 100% 100%;

  .topContainer{
    width: 100%;
    height: 70px;
    position: relative;

    .collapseContainer{
      width: 70px;
      height: 70px;
      padding: 20px;
      float: left;
    }

    .titleContainer{
      width: auto;
      height: 70px;
      font-size: xx-large;
      font-weight: bolder;
      line-height: 70px;
      letter-spacing: 5px;
      margin-left: 10px;
      float: left;
      background-image: -webkit-linear-gradient(left, #43bdf0, #c0d1f2 25%, #43bdf0 50%, #c0d1f2 75%, #43bdf0);
      -webkit-text-fill-color: transparent;
      background-clip: text;
      -webkit-background-clip: text;
      animation: masked-animation 4s infinite linear;
      @keyframes masked-animation {
        0% {
          background-position: 0 0;
        }
        100% {
          background-position: -100% 0;
        }
      }
    }

    .toolContainer{
      position: absolute;
      top: 15px;
      right: 10px;
      justify-content: flex-end;
      display: flex;
      align-items: center;
      color: white;
      font-size: small;
      font-weight: bold;

      :deep(.el-select-v2__wrapper){
        background-color: rgba(255, 255, 255, 0);
        .el-select-v2__placeholder {
          color: white;
        }
      }

      :deep(.el-input__wrapper){
        background-color: rgba(255, 255, 255, 0);
        i, input, span {
          color: white;
        }
      }
    }
  }

  .bottomContainer{
    position: relative;
    width: 100%;
    height: calc(100% - 70px);
    padding-left: 10px;
    padding-right: 10px;
    padding-bottom: 10px;
    overflow: hidden;


    .titleContainer{
      width: 100%;
      height: 25px;
      font-size: large;
      font-weight: bolder;
      line-height: 25px;
      letter-spacing: 3px;
      border-bottom: 1px solid #00eaff;
      background: linear-gradient(
              92deg,
              #0072ff 0%,
              #00eaff 48.8525390625%,
              #01aaff 100%
      );
      -webkit-background-clip: text;
      -webkit-text-fill-color: transparent;
      .unChosenTitle {
        background: linear-gradient(
                92deg,
                #004cad 0%,
                #006977 48.8525390625%,
                #00557c 100%
        );
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
      }
    }

    .selectContainer{
      position: absolute;
      top: 0;
      right: 15px;
      z-index: 5;
    }

    .mainContainer{
      width: 100%;
      height: calc(100% - 25px);
      padding: 5px;
      overflow: hidden;

      .el-statistic{
        --el-statistic-content-color: var(--el-color-primary-light-9);
      }

      .tableHeader{
        width: 100%;
        height: 30px;
        background: linear-gradient(
                92deg,
                #0072ff 0%,
                #00eaff 48.8525390625%,
                #01aaff 100%
        );
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        font-size: medium;
        font-weight: bolder;
        text-align: center;
        line-height: 30px;
        .el-col{
          border-bottom: 1px solid #fff;
        }

        .chosenElCol{
          border: 1px solid red;
        }
      }

      .tableItemContainer {
        width: 100%;
        height: auto;
        position: absolute;
        top: 0;
        left: 0;

        .tableItem {
          .el-col {
            border-bottom: 1px dashed #fff;
            overflow: hidden;
            text-overflow: ellipsis;
            white-space: nowrap;
          }
          .highLevel {
            color: red;
          }
          .middleLevel {
            color: orange;
          }
          .lowLevel {
            color: yellow;
          }
        }
      }

      .tableItem {
        width: 100%;
        height: 35px;
        color: white;
        font-weight: bolder;
        text-align: center;
        line-height: 35px;
        .el-col {
          overflow: hidden;
          text-overflow: ellipsis;
          white-space: nowrap;
        }
      }

    }

  }
}
</style>
