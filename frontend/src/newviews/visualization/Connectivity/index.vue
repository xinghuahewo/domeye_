<template>
  <div class="index" id="demo">
    <div class="topContainer">
      <div class="collapseContainer" v-show="!themeConfig.isFullScreen">
        <el-icon :color="'#fff'" :size="30" v-if="themeConfig.isCollapse" @click="ExpandOrFoldCollapse"><Expand /></el-icon>
        <el-icon :color="'#fff'" :size="30" v-else @click="ExpandOrFoldCollapse"><Fold /></el-icon>
      </div>
      <div class="titleContainer">国家联通关系态势感知平台</div>
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
        <span>联通地区：</span>
        <el-select-v2
            v-model="connectPlace"
            :options="connectPlaceOptions"
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
      <el-col :span="5" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">出口路径统计</span>
          </div>
          <div class="mainContainer">
            <AsideTop :data="LeftTopData"></AsideTop>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">出口路径概况</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="LeftCenterType">
              <el-radio label="国家" size="default" v-if="LeftCenterData.country.length > 1">国家</el-radio>
              <el-radio label="机构" size="default">机构</el-radio>
              <el-radio label="AS" size="default">AS</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <div id="LeftCenterChart" style="width: 100%;height: 100%" v-show="LeftCenterType !== 'AS'"></div>
            <el-row class="tableHeader" justify="space-between" v-show="LeftCenterType === 'AS'">
              <el-col :span="3">
                排名
              </el-col>
              <el-col :span="8">
                机构名称
              </el-col>
              <el-col :span="7">
                AS名称
              </el-col>
              <el-col :span="5">
                路径数量
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);" v-show="LeftCenterType === 'AS'">
              <el-row class="tableItem" v-for="(item, index) in LeftCenterData.as" :key="index" justify="space-between">
                <el-col :span="3">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="8">
                  {{ item.organization }}
                </el-col>
                <el-col :span="7">
                  {{ item.as }}
                </el-col>
                <el-col :span="5">
                  {{ item.path }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': LeftBottomType === 2 }" @click="chosenLeftBottomType(1)">境内绕道流量排行</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': LeftBottomType === 1 }" @click="chosenLeftBottomType(2)">境内绕道流量去向</span>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="LeftBottomType === 1 ? 5 : 4">
                排名
              </el-col>
              <el-col :span="7" v-if="LeftBottomType === 2">
                绕道地区
              </el-col>
              <el-col :span="LeftBottomType === 1 ? 10 : 6">
                {{ LeftBottomType === 1 ? '机构名称' : 'AS号' }}
              </el-col>
              <el-col :span="LeftBottomType === 1 ? 8 : 6">
                路径数量
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in LeftBottomType === 1 ? LeftBottomData.organization : LeftBottomData.area" :key="index" justify="space-between">
                <el-col :span="LeftBottomType === 1 ? 5 : 4">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="3" v-if="LeftBottomType === 2">
                  <span class="flag-icon" :class="item.countryFlag"></span>
                </el-col>
                <el-col :span="4" v-if="LeftBottomType === 2">
                  {{ item.countryName }}
                </el-col>
                <el-col :span="LeftBottomType === 1 ? 10 : 6">
                  {{ LeftBottomType === 1 ? item.organization : item.as }}
                </el-col>
                <el-col :span="LeftBottomType === 1 ? 8 : 6">
                  {{ LeftBottomType === 1 ? item.path : item.num }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :span="13" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 66.5%;padding: 5px">
          <div id="MainChart" style="width: 100%;height: 100%"></div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;float: left">
          <div class="titleContainer">
            <span style="margin-left: 10px">国家联通关系关键路径AS(Top6)</span>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="2">
                排行
              </el-col><el-col :span="3">
                AS号
              </el-col>
              <el-col :span="4">
                AS名称
              </el-col>
              <el-col :span="6">
                所属国家
              </el-col>
              <el-col :span="4">
                所属组织
              </el-col>
              <el-col :span="4">
                霸权值
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in CenterBottomData" :key="index" justify="space-between">
                <el-col :span="2">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="3">
                  {{ item.as }}
                </el-col>
                <el-col :span="4">
                  {{ item.AutName }}
                </el-col>
                <el-col :span="3">
                  <span class="flag-icon" :class="item.BelongCountryFlag"></span>
                </el-col>
                <el-col :span="3">
                  {{ item.BelongCountryName }}
                </el-col>
                <el-col :span="4">
                  {{ item.OrgName }}
                </el-col>
                <el-col :span="4">
                  {{ item.SupremacyNum }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :span="5" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">入口路径统计</span>
          </div>
          <div class="mainContainer">
            <AsideTop :data="RightTopData"></AsideTop>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">入口路径概况</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="RightCenterType">
              <el-radio label="国家" size="default" v-if="RightCenterData.country.length > 1">国家</el-radio>
              <el-radio label="机构" size="default">机构</el-radio>
              <el-radio label="AS" size="default">AS</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <div id="RightCenterChart" style="width: 100%;height: 100%" v-show="RightCenterType !== 'AS'"></div>
            <el-row class="tableHeader" justify="space-between" v-show="RightCenterType === 'AS'">
              <el-col :span="3">
                排名
              </el-col>
              <el-col :span="8">
                机构名称
              </el-col>
              <el-col :span="7">
                AS名称
              </el-col>
              <el-col :span="5">
                路径数量
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);" v-show="RightCenterType === 'AS'">
              <el-row class="tableItem" v-for="(item, index) in RightCenterData.as" :key="index" justify="space-between">
                <el-col :span="3">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="8">
                  {{ item.organization }}
                </el-col>
                <el-col :span="7">
                  {{ item.as }}
                </el-col>
                <el-col :span="5">
                  {{ item.path }}
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': RightBottomType === 2 }" @click="chosenRightBottomType(1)">境外借道流量排行</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': RightBottomType === 1 }" @click="chosenRightBottomType(2)">境外借道流量来源</span>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="RightBottomType === 1 ? 5 : 4">
                排名
              </el-col>
              <el-col :span="RightBottomType === 1 ? 10 : 7">
                {{ RightBottomType === 1 ? '借道地区' : '起始国家' }}
              </el-col>
              <el-col :span="7" v-if="RightBottomType === 2">
                终点国家
              </el-col>
              <el-col :span="RightBottomType === 1 ? 8 : 5">
                路径数量
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in RightBottomType === 2 ? RightBottomData.area : RightBottomData.country" :key="index" justify="space-between">
                <el-col :span="RightBottomType === 1 ? 5 : 4">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="RightBottomType === 1 ? 4 : 3">
                  <span class="flag-icon" :class="RightBottomType === 1 ? item.countryFlag : item.startCountryFlag"></span>
                </el-col>
                <el-col :span="RightBottomType === 1 ? 6 : 4">
                  {{ RightBottomType === 1 ? item.countryName : item.startCountry }}
                </el-col>
                <el-col :span="3" v-if="RightBottomType === 2">
                  <span class="flag-icon" :class="item.endCountryFlag"></span>
                </el-col>
                <el-col :span="4" v-if="RightBottomType === 2">
                  {{ item.endCountry }}
                </el-col>
                <el-col :span="RightBottomType === 1 ? 8 : 5">
                  {{ item.num }}
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
import {Expand, Fold, FullScreen} from '@element-plus/icons-vue'
import {useThemeConfig} from "/@/stores/themeConfig";
import {storeToRefs} from "pinia";
import {onMounted, reactive, ref, watch} from "vue";
import screenfull from "screenfull";
import {ElMessage} from "element-plus";
import router from "/@/router";
import * as echarts from "echarts";
import worldMapRemix from '/@/assets/worldJson/world-remix.json'
import AsideTop from '/@/newviews/visualization/Connectivity/AsideTop/index1.vue'
import FlagList from "/@/assets/flags/flag.json"
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


// 设置折叠logo
const storesThemeConfig = useThemeConfig();
const { themeConfig } = storeToRefs(storesThemeConfig);


// 选择大屏
const selectDemo = ref('国家联通关系')
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
const LeftCenterType = ref('')

let LeftCenterData = reactive({
  country: [],
  organization: [],
  as: []
})

let LeftCenterChart = null

const initLeftCenter = () => {
  if (LeftCenterChart != null && LeftCenterChart != "" && LeftCenterChart != undefined) {
    LeftCenterChart.dispose();//销毁
  }
  let chartDom = document.getElementById("LeftCenterChart")
  LeftCenterChart = echarts.init(chartDom)

  if(LeftCenterData.country.length > 1)
    LeftCenterType.value = '国家'
  else
    LeftCenterType.value = '机构'
}

const drawLeftCenter = () => {
  if(LeftCenterType.value === "国家"){
    LeftCenterChart.clear()

    let countryNameData = LeftCenterData.country.map((item) => { return item.countryName }).reverse()
    let countryFlagData = LeftCenterData.country.map((item) => { return item.countryFlag }).reverse()
    let countryNumData = LeftCenterData.country.map((item) => { return item.num }).reverse()

    let option = {
      grid: {
        top: '3%',
        left: '3%',
        right: '4%',
        bottom: '3%',
        containLabel: true
      },
      xAxis: {
        type: 'value'
      },
      yAxis: {
        type: 'category',
        data: countryNameData,
        axisLabel : {
          color: "#fff",
          formatter: function (value, index) {
            value = value.toString();
            let maxlength = 5;
            if (value.length > maxlength) {
              return "{"+countryFlagData[index]+"|}" + value.substring(0, maxlength - 1) + '...';
            } else {
              return "{"+countryFlagData[index]+"|}" + value
            }
          },
          rich: FlagList
        }
      },
      series: {
        name: 'Search Engine',
        type: 'bar',
        stack: 'total',
        label: {
          show: true
        },
        emphasis: {
          focus: 'series'
        },
        data: countryNumData,
        itemStyle: {
          normal: {
            //这里是重点
            color: function(params) {
              //注意，如果颜色太少的话，后面颜色不会自动循环，最好多定义几个颜色
              let colorList = [
                '#00faff',
                '#00e6ff',
                '#00d2ff',
                '#00beff',
                '#00aaff',
                '#0096ff',
                '#0082ff'
              ];
              return colorList[params.dataIndex]
            }
          }
        }
      }
    };
    option && LeftCenterChart.setOption(option);
  }
  else if(LeftCenterType.value === "机构"){
    LeftCenterChart.clear()
    let option = {
      tooltip: {
        trigger: 'item'
      },
      series: [
        {
          name: 'Access From',
          type: 'pie',
          radius: '80%',
          data: LeftCenterData.organization,
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: 'rgba(0, 0, 0, 0.5)'
            }
          }
        }
      ]
    };
    option && LeftCenterChart.setOption(option);
  }
}

watch(LeftCenterType, (NewVal) => {
  if(NewVal === "国家"){
    LeftCenterChart.clear()

    let countryNameData = LeftCenterData.country.map((item) => { return item.countryName }).reverse()
    let countryFlagData = LeftCenterData.country.map((item) => { return item.countryFlag }).reverse()
    let countryNumData = LeftCenterData.country.map((item) => { return item.num }).reverse()

    let option = {
      grid: {
        top: '3%',
        left: '3%',
        right: '4%',
        bottom: '3%',
        containLabel: true
      },
      xAxis: {
        type: 'value'
      },
      yAxis: {
        type: 'category',
        data: countryNameData,
        axisLabel : {
          color: "#fff",
          formatter: function (value, index) {
            value = value.toString();
            let maxlength = 5;
            if (value.length > maxlength) {
              return "{"+countryFlagData[index]+"|}" + value.substring(0, maxlength - 1) + '...';
            } else {
              return "{"+countryFlagData[index]+"|}" + value
            }
          },
          rich: FlagList
        }
      },
      series: {
        name: 'Search Engine',
        type: 'bar',
        stack: 'total',
        label: {
          show: true
        },
        emphasis: {
          focus: 'series'
        },
        data: countryNumData,
        itemStyle: {
          normal: {
            //这里是重点
            color: function(params) {
              //注意，如果颜色太少的话，后面颜色不会自动循环，最好多定义几个颜色
              let colorList = [
                '#00faff',
                '#00e6ff',
                '#00d2ff',
                '#00beff',
                '#00aaff',
                '#0096ff',
                '#0082ff'
              ];
              return colorList[params.dataIndex]
            }
          }
        }
      }
    };
    option && LeftCenterChart.setOption(option);
  }
  else if(NewVal === "机构"){
    LeftCenterChart.clear()
    let option = {
      tooltip: {
        trigger: 'item'
      },
      series: [
        {
          name: 'Access From',
          type: 'pie',
          radius: '80%',
          data: LeftCenterData.organization,
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: 'rgba(0, 0, 0, 0.5)'
            }
          }
        }
      ]
    };
    option && LeftCenterChart.setOption(option);
  }
})

// 左下
const LeftBottomType = ref(1)

let LeftBottomData = reactive({
  organization: [],
  area: [],
})

const chosenLeftBottomType = (val) => {
  LeftBottomType.value = val
}

// 中上
let MainChart = null

const CenterTopData = ref([])

const initMainMap = () => {
  if (MainChart != null && MainChart != "" && MainChart != undefined) {
    MainChart.dispose();//销毁
  }
  let chartDom = document.getElementById("MainChart")
  MainChart = echarts.init(chartDom)

  let series = [
    {
      type: 'lines',
      coordinateSystem: 'geo',
      label: {
        emphasis: {
          show: true,
          formatter: function (params) {
            //圆环显示文字
            return params.data.name + "-" + params.data.value
          },
        }
      },
      zlevel: 2,
      effect: {
        show: true,
        period: 5, //箭头指向速度，值越小速度越快
        trailLength: 0, //特效尾迹长度[0,1]值越大，尾迹越长重
        symbol: 'arrow', //箭头图标
        symbolSize: 5, //图标大小
        color: '#fcdd6e', // 图标颜色
      },
      lineStyle: {
        show: true,
        opacity: 1, //尾迹线条透明度
        curveness: 0.3, //尾迹线条曲直度
        color: function (params) {
          if (params.data.value > 100)
              return '#DB3124FF'
          else if (params.data.value > 50)
            return '#FC8C5AFF'
          else
            return '#FDE8D5FF'
        },
      },
      data: CenterTopData.value.map(function (dataItem) {
        return [
          {
            coord: [ dataItem.ExportLng > -30 ? dataItem.ExportLng - 180 : dataItem.ExportLng + 180, dataItem.ExportLat],
          },
          {
            coord: [ dataItem.ImportLng > -30 ? dataItem.ImportLng - 180 : dataItem.ImportLng + 180, dataItem.ImportLat],
            name: dataItem.ImportCountry,
            value: dataItem.t,
            lineStyle: {
              width: dataItem.t > 100 ? 1.2 : (dataItem.t > 50 ? 0.8 : 0.4)
            },
          },
        ]
      }),
    },
    {
      type: 'effectScatter',
      coordinateSystem: 'geo',
      zlevel: 2,
      rippleEffect: {
        //涟漪特效
        period: 4, //动画时间，值越小速度越快
        brushType: 'stroke', //波纹绘制方式 stroke, fill
        scale: 3, //波纹圆环最大限制，值越大波纹越大
        color: '#fcdd6e',
      },
      labelLayout: {
        hideOverlap: true,
      },
      label: {
        normal:{
          position: 'right', //显示位置
          offset: [5, 0], //偏移设置
          formatter: function (params) {
            //圆环显示文字
            return params.data.name
          },
          fontSize: 13,
        },
      },
      symbol: 'circle',
      symbolSize: function () {
        return 5 //圆环大小
      },
      itemStyle: {
        normal: {
          show: false,
          color: '#fce182',
        },
      },
      data: CenterTopData.value.map(function (dataItem) {
        return {
          name: dataItem.ImportCountry,
          value: [ dataItem.ImportLng > -30 ? dataItem.ImportLng - 180 : dataItem.ImportLng + 180, dataItem.ImportLat],
          label: {
            normal:{
              show: dataItem.t > 150
            },
          }
        }
      })
    },
    {
      type: 'effectScatter',
      coordinateSystem: 'geo',
      zlevel: 15,
      rippleEffect: {
        period: 4,
        brushType: 'stroke',
        scale: 4,
        color: '#38ff85',
      },
      label: {
        show: false,
      },
      symbol: 'circle',
      symbolSize: 5,
      itemStyle: {
        color: '#38ff85',
      },
      data: [
        {
          name: CenterTopData.value[0].ExportCountry,
          value: [ CenterTopData.value[0].ExportLng > -30 ? CenterTopData.value[0].ExportLng - 180 : CenterTopData.value[0].ExportLng + 180, CenterTopData.value[0].ExportLat],
        },
      ]
    },
  ]

  let option = {
    geo: {
      map: 'world',
      zoom: 1.25,
      silent: true,
      label: {
        emphasis: {
          show: false
        }
      },
      roam: true, // 是否开启鼠标缩放和平移漫游
      // 地图的背景色
      itemStyle: {
        normal: {
          areaColor: '#5781b6',
          borderColor: 'rgb(45,48,96)',
        }
      },
    },
    series: series,
  }
  MainChart.setOption(option)
}

// 中下
const CenterBottomData = ref([])

// 右上
const RightTopData = ref([])


// 右中
const RightCenterType = ref()

let RightCenterData = reactive({
  country: [],
  organization: [],
  as: []
})

let RightCenterChart = null

const initRightCenter = () => {
  if (RightCenterChart != null && RightCenterChart != "" && RightCenterChart != undefined) {
    RightCenterChart.dispose();//销毁
  }
  let chartDom = document.getElementById("RightCenterChart")
  RightCenterChart = echarts.init(chartDom)

  if(RightCenterData.country.length > 1)
    RightCenterType.value = '国家'
  else
    RightCenterType.value = '机构'
}

const drawRightCenter = () => {
  if(RightCenterType.value === "国家"){
    RightCenterChart.clear()

    let countryNameData = RightCenterData.country.map((item) => { return item.countryName }).reverse()
    let countryFlagData = RightCenterData.country.map((item) => { return item.countryFlag }).reverse()
    let countryNumData = RightCenterData.country.map((item) => { return item.num }).reverse()

    let option = {
      grid: {
        top: '3%',
        left: '3%',
        right: '4%',
        bottom: '3%',
        containLabel: true
      },
      xAxis: {
        type: 'value'
      },
      yAxis: {
        type: 'category',
        data: countryNameData,
        axisLabel : {
          color: "#fff",
          formatter: function (value, index) {
            value = value.toString();
            let maxlength = 5;
            if (value.length > maxlength) {
              return "{"+countryFlagData[index]+"|}" + value.substring(0, maxlength - 1) + '...';
            } else {
              return "{"+countryFlagData[index]+"|}" + value
            }
          },
          rich: FlagList
        }
      },
      series: {
        name: 'Search Engine',
        type: 'bar',
        stack: 'total',
        label: {
          show: true
        },
        emphasis: {
          focus: 'series'
        },
        data: countryNumData,
        itemStyle: {
          normal: {
            //这里是重点
            color: function(params) {
              //注意，如果颜色太少的话，后面颜色不会自动循环，最好多定义几个颜色
              let colorList = [
                '#00faff',
                '#00e6ff',
                '#00d2ff',
                '#00beff',
                '#00aaff',
                '#0096ff',
                '#0082ff'
              ];
              return colorList[params.dataIndex]
            }
          }
        }
      }
    };
    option && RightCenterChart.setOption(option);
  }
  else if(RightCenterType.value === "机构"){
    RightCenterChart.clear()
    let option = {
      tooltip: {
        trigger: 'item'
      },
      series: [
        {
          name: 'Access From',
          type: 'pie',
          radius: '80%',
          data: RightCenterData.organization,
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: 'rgba(0, 0, 0, 0.5)'
            }
          }
        }
      ]
    };
    option && RightCenterChart.setOption(option);
  }
}

watch(RightCenterType, (NewVal) => {
  if(NewVal === "国家"){
    RightCenterChart.clear()

    let countryNameData = RightCenterData.country.map((item) => { return item.countryName }).reverse()
    let countryFlagData = RightCenterData.country.map((item) => { return item.countryFlag }).reverse()
    let countryNumData = RightCenterData.country.map((item) => { return item.num }).reverse()

    let option = {
      grid: {
        top: '3%',
        left: '3%',
        right: '4%',
        bottom: '3%',
        containLabel: true
      },
      xAxis: {
        type: 'value'
      },
      yAxis: {
        type: 'category',
        data: countryNameData,
        axisLabel : {
          color: "#fff",
          formatter: function (value, index) {
            value = value.toString();
            let maxlength = 5;
            if (value.length > maxlength) {
              return "{"+countryFlagData[index]+"|}" + value.substring(0, maxlength - 1) + '...';
            } else {
              return "{"+countryFlagData[index]+"|}" + value
            }
          },
          rich: FlagList
        }
      },
      series: {
        name: 'Search Engine',
        type: 'bar',
        stack: 'total',
        label: {
          show: true
        },
        emphasis: {
          focus: 'series'
        },
        data: countryNumData,
        itemStyle: {
          normal: {
            //这里是重点
            color: function(params) {
              //注意，如果颜色太少的话，后面颜色不会自动循环，最好多定义几个颜色
              let colorList = [
                '#00faff',
                '#00e6ff',
                '#00d2ff',
                '#00beff',
                '#00aaff',
                '#0096ff',
                '#0082ff'
              ];
              return colorList[params.dataIndex]
            }
          }
        }
      }
    };
    option && RightCenterChart.setOption(option);
  }
  else if(NewVal === "机构"){
    RightCenterChart.clear()
    let option = {
      tooltip: {
        trigger: 'item'
      },
      series: [
        {
          name: 'Access From',
          type: 'pie',
          radius: '80%',
          data: RightCenterData.organization,
          emphasis: {
            itemStyle: {
              shadowBlur: 10,
              shadowOffsetX: 0,
              shadowColor: 'rgba(0, 0, 0, 0.5)'
            }
          }
        }
      ]
    };
    option && RightCenterChart.setOption(option);
  }
})

// 右下
const RightBottomType = ref(1)

let RightBottomData = reactive({
  country: [],
  area: []
})

const chosenRightBottomType = (val) => {
  RightBottomType.value = val
}


// 折叠切换
const ExpandOrFoldCollapse = () => {
  themeConfig.value.isCollapse = !themeConfig.value.isCollapse;
  setTimeout(() => {
    LeftCenterChart.resize()
    MainChart.resize()
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
      MainChart.resize()
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
const connectPlace = ref('')

const viewPlaceOptions = ref([])
const connectPlaceOptions = ref([])

watch(connectPlace, (NewVal) => {
  ShowData.value = AllData.value[NewVal]

  LeftTopData.value = ShowData.value.LeftTopData

  LeftCenterData = ShowData.value.LeftCenterData
  if(LeftCenterData.country.length > 6)
    LeftCenterData.country = LeftCenterData.country.slice(0, 6)
  if(LeftCenterData.as.length > 6)
    LeftCenterData.as = LeftCenterData.as.slice(0, 6)
  initLeftCenter()
  drawLeftCenter()

  LeftBottomData = ShowData.value.LeftBottomData
  if(LeftBottomData.organization.length > 6)
    LeftBottomData.organization = LeftBottomData.organization.slice(0, 6)
  if(LeftBottomData.area.length > 6)
    LeftBottomData.area = LeftBottomData.area.slice(0, 6)

  CenterTopData.value = ShowData.value.CenterTopData
  initMainMap()

  if(ShowData.value.CenterBottomData.SupremacyRank.length > 6)
    CenterBottomData.value = ShowData.value.CenterBottomData.SupremacyRank.slice(0, 6)

  RightTopData.value = ShowData.value.RightTopData

  RightCenterData = ShowData.value.RightCenterData
  if(RightCenterData.country.length > 6)
    RightCenterData.country = RightCenterData.country.slice(0, 6)
  if(RightCenterData.as.length > 6)
    RightCenterData.as = RightCenterData.as.slice(0, 6)
  initRightCenter()
  drawRightCenter()

  RightBottomData = ShowData.value.RightBottomData
  if(RightBottomData.country.length > 6)
    RightBottomData.country = RightBottomData.country.slice(0, 6)
  if(RightBottomData.area.length > 6)
    RightBottomData.area = RightBottomData.area.slice(0, 6)

})

const initShowData = async () => {
  const screenfileData = await request({
    url: 'geodata/connections/screenfile',
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

  connectPlaceOptions.value = Object.keys(AllData.value).map((item) => {
    return {
      label: item,
      value: item
    }
  })
  connectPlace.value = connectPlaceOptions.value[0].value
}

onMounted(async () => {

  echarts.registerMap('world',  worldMapRemix)

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
        i, input {
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
