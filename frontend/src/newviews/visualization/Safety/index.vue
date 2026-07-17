<template>
  <div class="index" id="demo">
    <div class="topContainer">
      <div class="collapseContainer" v-show="!themeConfig.isFullScreen">
        <el-icon :color="'#fff'" :size="30" v-if="themeConfig.isCollapse" @click="ExpandOrFoldCollapse"><Expand /></el-icon>
        <el-icon :color="'#fff'" :size="30" v-else @click="ExpandOrFoldCollapse"><Fold /></el-icon>
      </div>
      <div class="titleContainer">互联网路由安全态势感知平台</div>
      <div class="toolContainer">
        <span>观测地区：</span>
        <el-select-v2
            v-model="selectPlace"
            :options="placeOptions"
            placeholder="Please select"
            size="default"
            :clearable="false"
            style="width: 90px;margin-right: 10px"
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
        <el-icon :color="'#fff'" :size="25" @click="SwitchBigScreen"><FullScreen /></el-icon>
      </div>
    </div>

    <el-row class="bottomContainer" justify="space-around" align="middle">
      <el-col :span="5" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': LeftTopTitle !== 1 }" @click="chosenLeftTopTitle(1)">安全态势指数</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': LeftTopTitle !== 2 }" @click="chosenLeftTopTitle(2)">告警统计</span>
          </div>
          <div class="mainContainer">
            <div id="LeftTopChart" style="width: 100%;height: 100%"></div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%">
          <div class="titleContainer">
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': LeftCenterTitle === 2 }" @click="chosenLeftCenterTitle(1)">机构异常(Top6)</span>
            <span>&nbsp;|&nbsp;</span>
            <span :class="{ 'unChosenTitle': LeftCenterTitle === 1 }" @click="chosenLeftCenterTitle(2)">AS异常(Top6)</span>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="3">
                排名
              </el-col>
              <el-col :span="4">
                地区
              </el-col>
              <el-col :span="9">
                {{ LeftCenterTitle === 1 ? '机构名称' : 'AS名称' }}
              </el-col>
              <el-col :span="7">
                异常事件数量
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in showLeftCenterData()" :key="index" justify="space-between">
                <el-col :span="3">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="2">
                  <span class="flag-icon" :class="item.flag"></span>
                </el-col>
                <el-col :span="2">
                  {{ item.attacked_country }}
                </el-col>
                <el-col :span="9">
                  {{ item.attacked }}
                </el-col>
                <el-col :span="7">
                  <el-progress @click="clickToShow(item)" :text-inside="true" :color="colorData[index]" :percentage="(parseFloat(item.num)/showLeftCenterData()[0].num).toFixed(2)*100" :show-text="false" :stroke-width="20" style="margin-top: 10px">
                    {{ item.num }}
                  </el-progress>
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">事件态势图</span>
          </div>
          <div class="mainContainer" id="LeftBottomChart" style="padding-top: 5px"></div>
        </div>
      </el-col>

      <el-col :span="13" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 66%;padding: 5px">
          <div id="CenterTopChart" style="width: 100%;height: 100%"></div>
        </div>
        <div style="width: 100%;height: 33.5%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">事件轮播</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="judgeType" style="margin-right: 20px">
              <el-radio label="误报" size="default">误报</el-radio>
              <el-radio label="异常" size="default">异常</el-radio>
            </el-radio-group>

            <span style="color: white;font-size:20px;line-height: 25px ">|</span>

            <el-radio-group v-model="thingLevel" style="margin-left: 20px">
              <el-radio label="高" size="default">高等级</el-radio>
              <el-radio label="中" size="default">中等级</el-radio>
              <el-radio label="低" size="default">低等级</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="7">
                受害方信息
              </el-col>
              <el-col :span="7">
                肇事方信息
              </el-col>
              <el-col :span="4">
                时间
              </el-col>
              <el-col :span="5">
                事件信息
              </el-col>
            </el-row>
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="3">
                地区
              </el-col>
              <el-col :span="2">
                机构名称
              </el-col>
              <el-col :span="2">
                AS号
              </el-col>
              <el-col :span="3">
                地区
              </el-col>
              <el-col :span="2">
                机构名称
              </el-col>
              <el-col :span="2">
                AS号
              </el-col>
              <el-col :span="2">
                开始时间
              </el-col>
              <el-col :span="2">
                结束时间
              </el-col>
              <el-col :span="2">
                事件类型
              </el-col>
              <el-col :span="2">
                研判结果
              </el-col>
              <el-col :span="1">
                等级
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 60px);overflow: hidden;position: relative;">
              <div class="tableItemContainer" id="tableItemContainer">
                <el-row class="tableItem" v-for="(item, index) in tableData.showData" :key="index" justify="space-between">
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackedFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedAS }}
                  </el-col>
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackerFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerAS }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.startTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.endTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventType }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventJudge }}
                  </el-col>
                  <el-col :span="1" :class="{highLevel: item.eventLevel === '高', middleLevel: item.eventLevel === '中', lowLevel: item.eventLevel === '低'}">
                    {{ item.eventLevel }}
                  </el-col>
                </el-row>
                <el-row class="tableItem" v-for="(item, index) in tableData.showData" :key="index" justify="space-between">
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackedFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedAS }}
                  </el-col>
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackerFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerAS }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.startTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.endTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventType }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventJudge }}
                  </el-col>
                  <el-col :span="1" :class="{highLevel: item.eventLevel === '高', middleLevel: item.eventLevel === '中', lowLevel: item.eventLevel === '低'}">
                    {{ item.eventLevel }}
                  </el-col>
                </el-row>
                <el-row class="tableItem" v-for="(item, index) in tableData.showData" :key="index" justify="space-between">
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackedFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedAS }}
                  </el-col>
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackerFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerAS }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.startTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.endTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventType }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventJudge }}
                  </el-col>
                  <el-col :span="1" :class="{highLevel: item.eventLevel === '高', middleLevel: item.eventLevel === '中', lowLevel: item.eventLevel === '低'}">
                    {{ item.eventLevel }}
                  </el-col>
                </el-row>
                <el-row class="tableItem" v-for="(item, index) in tableData.showData" :key="index" justify="space-between">
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackedFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedAS }}
                  </el-col>
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackerFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerAS }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.startTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.endTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventType }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventJudge }}
                  </el-col>
                  <el-col :span="1" :class="{highLevel: item.eventLevel === '高', middleLevel: item.eventLevel === '中', lowLevel: item.eventLevel === '低'}">
                    {{ item.eventLevel }}
                  </el-col>
                </el-row>
                <el-row class="tableItem" v-for="(item, index) in tableData.showData" :key="index" justify="space-between">
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackedFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackedAS }}
                  </el-col>
                  <el-col :span="1">
                    <span class="flag-icon" :class="item.attackerFlag"></span>
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerCountry }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerOrg }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.attackerAS }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.startTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.endTime }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventType }}
                  </el-col>
                  <el-col :span="2">
                    {{ item.eventJudge }}
                  </el-col>
                  <el-col :span="1" :class="{highLevel: item.eventLevel === '高', middleLevel: item.eventLevel === '中', lowLevel: item.eventLevel === '低'}">
                    {{ item.eventLevel }}
                  </el-col>
                </el-row>
              </div>
            </div>
          </div>
        </div>
      </el-col>

      <el-col :span="5" style="width: 100%;height: 100%;">
        <div style="width: 100%;height: 33%;padding: 5px;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">事件态势概况</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="staticType">
              <el-radio label="类型" size="default">类型</el-radio>
              <el-radio label="程度" size="default">程度</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <el-row style="width: 100%;height: 70px;margin-top: 5px;text-align: center" justify="space-around" v-if="staticType === '类型'">
              <el-col :span="6" v-for="(item, index) in (selectPlace === '全球'? thingsData.world.typeData: thingsData.china.typeData)" :key="index">
                <div>
                  <el-statistic :value="item.num" style="color: white">
                    <template #title>
                      <div style="display: inline-flex; align-items: center; color: white">
                        {{ item.event_type }}
                      </div>
                    </template>
                  </el-statistic>
                  <div>
                    <div v-if="item.amplitude_type">
                    <span style="color: #d41a1a">
                      {{ item.amplitude }}
                      <el-icon :color="'#d41a1a'">
                        <CaretTop />
                      </el-icon>
                    </span>
                    </div>
                    <div v-else>
                    <span style="color: #1ad45b">
                      {{ item.amplitude }}
                      <el-icon :color="'#1ad45b'">
                        <CaretBottom />
                      </el-icon>
                    </span>
                    </div>
                  </div>
                </div>
              </el-col>
            </el-row>
            <el-row style="width: 100%;height: 70px;margin-top: 5px;text-align: center" justify="space-around" v-else>
              <el-col :span="8" v-for="(item, index) in (selectPlace === '全球'? thingsData.world.levelData: thingsData.china.levelData)" :key="index">
                <div>
                  <el-statistic :value="item.num" style="color: white">
                    <template #title>
                      <div style="display: inline-flex; align-items: center; color: white">
                        {{ item.level }}
                      </div>
                    </template>
                  </el-statistic>
                  <div>
                    <div v-if="item.amplitude_type">
                    <span style="color: #d41a1a">
                      {{ item.amplitude }}
                      <el-icon :color="'#d41a1a'">
                        <CaretTop />
                      </el-icon>
                    </span>
                    </div>
                    <div v-else>
                    <span style="color: #1ad45b">
                      {{ item.amplitude }}
                      <el-icon :color="'#1ad45b'">
                        <CaretBottom />
                      </el-icon>
                    </span>
                    </div>
                  </div>
                </div>
              </el-col>
            </el-row>
            <div id="RightTopChart" style="width: 100%;height: calc(100% - 75px)"></div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%">
          <div class="titleContainer">
            <span :class="{ 'unChosenTitle': RightCenterTitle === 1 }" @click="chosenRightCenterTitle(2)">机构AS资源排行</span>
            <span>&nbsp;|&nbsp;</span>
            <span style="margin-left: 10px" :class="{ 'unChosenTitle': RightCenterTitle === 2 }" @click="chosenRightCenterTitle(1)">机构IP资源排行</span>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="3">
                排名
              </el-col>
              <el-col :span="4">
                地区
              </el-col>
              <el-col :span="9">
                机构名称
              </el-col>
              <el-col :span="7">
                {{ RightCenterTitle === 1 ? 'IP数量' : 'AS数量' }}
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in RightCenterData.showData" :key="index" justify="space-between">
                <el-col :span="3">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="2">
                  <span class="flag-icon" :class="item.flag"></span>
                </el-col>
                <el-col :span="2">
                  {{ item.country }}
                </el-col>
                <el-col :span="9">
                  {{ item.organization }}
                </el-col>
                <el-col :span="7">
                  <el-progress :text-inside="true" :color="RightCenterData.colorData[index]" :percentage="(parseFloat(item.num)/RightCenterData.showData[0].num).toFixed(2)*100" :show-text="false" :stroke-width="20" style="margin-top: 10px">
                    {{ item.num }}
                  </el-progress>
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
        <div style="width: 100%;height: 33%;padding: 5px;margin-top: 0.5%;position: relative">
          <div class="titleContainer">
            <span style="margin-left: 10px">恶意AS排行(Top6)</span>
          </div>
          <div class="selectContainer">
            <el-radio-group v-model="RightBottomTitle">
              <el-radio label="长时" size="default">长时</el-radio>
              <el-radio label="短时" size="default">短时</el-radio>
            </el-radio-group>
          </div>
          <div class="mainContainer">
            <el-row class="tableHeader" justify="space-between">
              <el-col :span="3">
                排名
              </el-col>
              <el-col :span="4">
                地区
              </el-col>
              <el-col :span="6">
                机构名称
              </el-col>
              <el-col :span="5">
                AS名称
              </el-col>
              <el-col :span="5">
                恶意分数
              </el-col>
            </el-row>
            <div style="width: 100%;height: calc(100% - 30px);">
              <el-row class="tableItem" v-for="(item, index) in (RightBottomTitle === '长时' ? (selectPlace === '全球' ? RightBottomData.worldData.longTimeData : RightBottomData.chinaData.longTimeData) : (selectPlace === '全球' ? RightBottomData.worldData.shortTimeData : RightBottomData.chinaData.shortTimeData))" :key="index" justify="space-between">
                <el-col :span="3">
                  {{ index + 1 }}
                </el-col>
                <el-col :span="2">
                  <span class="flag-icon" :class="item.flag"></span>
                </el-col>
                <el-col :span="2">
                  {{ item.country }}
                </el-col>
                <el-col :span="6">
                  {{ item.organization }}
                </el-col>
                <el-col :span="5">
                  {{ item.as }}
                </el-col>
                <el-col :span="5" @click="JumpToDetail((RightBottomTitle === '长时' ? '/malicious/long_detail' : '/malicious/short_detail'), item.as)">
                  <el-progress :text-inside="true" :color="RightCenterData.colorData[index]" :percentage="item.num" :show-text="false" :stroke-width="20" style="margin-top: 10px">
                    {{ item.num.toFixed(2) }}
                  </el-progress>
                </el-col>
              </el-row>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-dialog v-model="dialogTableVisible" title="Shipping address">
      <template #header>
        <div class="my-header">
          <span class="flag-icon" :class="showDetailData.flag"></span>
          <span>  {{ " " + showDetailData.attacked_country }} - {{ showDetailData.attacked }}</span>
        </div>
      </template>
      <div id="DetailCharts" style="width: 100%;height: 300px"></div>
    </el-dialog>
  </div>
</template>

<script setup lang="ts">
import {Expand, Fold, FullScreen, CaretTop, CaretBottom} from '@element-plus/icons-vue'
import {useThemeConfig} from "/@/stores/themeConfig";
import {storeToRefs} from "pinia";
import {onMounted, reactive, ref, watch} from "vue";
import router from "/@/router";
import screenfull from "screenfull";
import {ElMessage} from "element-plus";
import * as echarts from "echarts"
import worldMapRemix from '/@/assets/worldJson/world-remix.json'
import cn_map from '/@/assets/worldJson/cn_map.json'
import request from "/@/utils/request";
import baseUrl from "/@/api";
import LongTimeData from '/@/assets/Malicious/blacklist_long.json'
import ShortTimeData from '/@/assets/Malicious/blacklist_short.json'


// 设置折叠logo
const storesThemeConfig = useThemeConfig();
const { themeConfig } = storeToRefs(storesThemeConfig);


// 选择范围
const selectPlace = ref('全球')
const placeOptions = [
  {
    label: '全球',
    value: '全球',
  },
  {
    label: '中国',
    value: '中国',
  },
]


// 选择大屏
const selectDemo = ref('互联网路由安全')
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

// 颜色数组
const colorData = ref([
  '#0082ff',
  '#0096ff',
  '#00aaff',
  '#00beff',
  '#00d2ff',
  '#00e6ff',
  '#00faff'
])

// 左上
const LeftTopTitle = ref(1)

const LeftTopData = reactive({
  world: {
    num: '68',
    allStatic: 5633,
    static: [
      {
        name: '已研判',
        value: 5288,
        itemStyle: {
          color: '#1a5dd4'
        },
        children: [
          {
            name: '误报事件',
            value: 2395,
            itemStyle: {
              color: '#1ad45b'
            },
          },
          {
            name: '异常事件',
            value: 1197,
            itemStyle: {
              color: '#d41a1a'
            },
          },
          {
            name: '疑似事件',
            value: 1696,
            itemStyle: {
              color: '#d4831a'
            },
          }
        ]
      },
      {
        name: '待研判',
        value: 345,
        itemStyle: {
          color: 'rgb(153,169,191)'
        }
      }
    ]
  },
  china: {
    num: '79',
    allStatic: 57,
    static: [
      {
        name: '已研判',
        value: 53,
        itemStyle: {
          color: '#1a5dd4'
        },
        children: [
          {
            name: '误报事件',
            value: 24,
            itemStyle: {
              color: '#1ad45b'
            },
          },
          {
            name: '异常事件',
            value: 12,
            itemStyle: {
              color: '#d41a1a'
            },
          },
          {
            name: '疑似事件',
            value: 17,
            itemStyle: {
              color: '#d4831a'
            },
          }
        ]
      },
      {
        name: '待研判',
        value: 4,
        itemStyle: {
          color: 'rgb(153,169,191)'
        }
      }
    ]
  },
  showData: {}
})

const chosenLeftTopTitle = (value) => {
  LeftTopTitle.value = value
}

let LeftTopChart = null;

const initLeftTop = () => {
  if (LeftTopChart != null && LeftTopChart != "" && LeftTopChart != undefined) {
    LeftTopChart.dispose();//销毁
  }
  LeftTopData.showData = LeftTopData.world
  let chartDom = document.getElementById('LeftTopChart');
  LeftTopChart = echarts.init(chartDom);
  let option = {
    grid: {
      top: '3%',
      left: '2%',
      right: '3%',
      bottom: '2%',
      containLabel: true
    },
    series: [
      {
        type: 'gauge',
        startAngle: 180,
        endAngle: 0,
        center: ['50%', '80%'],
        radius: '100%',
        min: 0,
        max: 1,
        splitNumber: 8,
        axisLine: {
          lineStyle: {
            width: 5,
            color: [
              [0.3, '#FF6E76'],
              [0.7, '#FDDD60'],
              [1, '#7CFFB2']
            ]
          }
        },
        pointer: {
          icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
          length: '10%',
          width: 12,
          offsetCenter: [0, '-55%'],
          itemStyle: {
            color: 'auto'
          }
        },
        axisTick: {
          length: 12,
          lineStyle: {
            color: 'auto',
            width: 1
          }
        },
        splitLine: {
          length: 12,
          lineStyle: {
            color: 'auto',
            width: 2
          }
        },
        axisLabel: {
          color: '#ffffff',
          fontSize: 15,
          distance: -35,
          rotate: 'tangential',
          formatter: function (value) {
            if (value === 0.875) {
              return '优秀';
            } else if (value === 0.5) {
              return '良好';
            } else if (value === 0.125) {
              return '一般';
            }
            return '';
          }
        },
        title: {
          color: '#ffffff',
          offsetCenter: [0, '-10%'],
          fontSize: 12
        },
        detail: {
          fontSize: 24,
          offsetCenter: [0, '-35%'],
          valueAnimation: true,
          formatter: function (value) {
            return Math.round(value * 100) + '';
          },
          color: 'inherit'
        },
        data: [
          {
            value: (LeftTopData.showData.num/100.0).toFixed(2),
            name: '安全态势系数'
          }
        ]
      }
    ]
  }
  LeftTopChart.setOption(option);
}

watch(LeftTopTitle, (NewVal) => {
  LeftTopChart.clear()
  if(NewVal === 1) {
    let option = {
      grid: {
        top: '3%',
        left: '2%',
        right: '3%',
        bottom: '2%',
        containLabel: true
      },
      series: [
        {
          type: 'gauge',
          startAngle: 180,
          endAngle: 0,
          center: ['50%', '80%'],
          radius: '100%',
          min: 0,
          max: 1,
          splitNumber: 8,
          axisLine: {
            lineStyle: {
              width: 5,
              color: [
                [0.3, '#FF6E76'],
                [0.7, '#FDDD60'],
                [1, '#7CFFB2']
              ]
            }
          },
          pointer: {
            icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
            length: '10%',
            width: 12,
            offsetCenter: [0, '-55%'],
            itemStyle: {
              color: 'auto'
            }
          },
          axisTick: {
            length: 12,
            lineStyle: {
              color: 'auto',
              width: 1
            }
          },
          splitLine: {
            length: 12,
            lineStyle: {
              color: 'auto',
              width: 2
            }
          },
          axisLabel: {
            color: '#ffffff',
            fontSize: 15,
            distance: -35,
            rotate: 'tangential',
            formatter: function (value) {
              if (value === 0.875) {
                return '优秀';
              } else if (value === 0.5) {
                return '良好';
              } else if (value === 0.125) {
                return '一般';
              }
              return '';
            }
          },
          title: {
            color: '#ffffff',
            offsetCenter: [0, '-10%'],
            fontSize: 12
          },
          detail: {
            fontSize: 24,
            offsetCenter: [0, '-35%'],
            valueAnimation: true,
            formatter: function (value) {
              return Math.round(value * 100) + '';
            },
            color: 'inherit'
          },
          data: [
            {
              value: (LeftTopData.showData.num/100.0).toFixed(2),
              name: '安全态势系数'
            }
          ]
        }
      ]
    }
    LeftTopChart.setOption(option);
  }
  else if(NewVal === 2) {
    let option = {
      title: {
        top: "center",
        left: "center",
        text: LeftTopData.showData.allStatic.toString() + '\n' + '总数',
        textStyle: {
          color: "#ffffff",
          lineHeight: 20,
          fontSize: 14,
          padding:[14,0,0,0]
        },
      },
      tooltip: {
        trigger: 'item'
      },
      series: {
        type: 'sunburst',
        data: LeftTopData.showData.static,
        radius: ['40%', '95%'],
        center: ['50%', '50%'],
        itemStyle: {
          borderRadius: 10,
          borderWidth: 2
        },
        label: {
          show: true,
          rotate: 'radial',
          color: 'white',
          fontSize: 10,
        },
      }
    }
    LeftTopChart.setOption(option);
  }
})


// 左中
const dialogTableVisible = ref(false)

const showDetailData = ref({
  attacked: "",
  attacked_country: "",
  flag: "",
  num: 0,
  thingsList: []
})

const clickToShow = (val) => {
  showDetailData.value = val
  dialogTableVisible.value = true

  let option = {
    title: {
      top: "center",
      left: "center",
      text: showDetailData.value.num.toString() + '\n' + '总数',
      textStyle: {
        color: "#1a5dd4",
        lineHeight: 20,
        fontSize: 14,
        padding: [14, 0, 0, 0]
      },
    },
    tooltip: {
      trigger: 'item'
    },
    series: [
      {
        type: 'pie',
        radius: ['40%', '70%'],
        itemStyle: {
          borderRadius: 10,
          borderColor: '#fff',
          borderWidth: 2
        },
        data: showDetailData.value.thingsList,
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
  setTimeout(() => {
    let chartDom = document.getElementById('DetailCharts')!;
    let myChart = echarts.init(chartDom);
    myChart.clear()
    option && myChart.setOption(option);
  }, 200)
}

const LeftCenterTitle = ref(1)

const chosenLeftCenterTitle = (value) => {
  LeftCenterTitle.value=value
}

const LeftCenterData = ref({
  world: {
    asData: [],
    organizationData: []
  },
  china: {
    asData: [],
    organizationData: []
  }
})

const showLeftCenterData = () => {
  if(LeftCenterTitle.value === 1){
    if(selectPlace.value === '全球')
      return LeftCenterData.value.world.organizationData
    else
      return LeftCenterData.value.china.organizationData
  }
  else{
    if(selectPlace.value === '全球')
      return LeftCenterData.value.world.asData
    else
      return LeftCenterData.value.china.asData
  }
}

// 左下
const LeftBottomData = ref({
  world: [],
  china: []
})

let LeftBottomChart = null

const drawLeftBottomChart = () => {
  let TimeList = (selectPlace.value === '全球' ? LeftBottomData.value.world : LeftBottomData.value.china).map(
      (item) => item.time
  )
  let NumList = (selectPlace.value === '全球' ? LeftBottomData.value.world : LeftBottomData.value.china).map(
      (item) => item.num
  )

  let chartDom = document.getElementById('LeftBottomChart');
  LeftBottomChart = echarts.init(chartDom);
  let option = {
    textStyle: {
      color: '#fff'
    },
    tooltip: {
      trigger: 'item'
    },
    grid: {
      top: '5%',
      left: 0,
      right: 0,
      bottom: 0,
      containLabel: true
    },
    xAxis: {
      type: 'category',
      data: TimeList.reverse(),
    },
    yAxis: {
      type: 'value'
    },
    series: [
      {
        data: NumList.reverse(),
        type: 'line',
        itemStyle: {
          normal: {
            color: '#ff0000', //改变折线点的颜色
            lineStyle: {
              color: '#ff0000' //改变折线颜色
            }
          }
        },
        smooth: true
      }
    ]
  };
  option && LeftBottomChart.setOption(option);
}

// 中上
const CenterTopData = ref({
  world: [],
  china: [],
})

let CenterTopChart = null

// 全球地图绘制函数
const drawWorldMap = () => {
  CenterTopChart.clear()
  let option = {
    geo: {
      map: 'world',
      zoom: 1.3,
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
          // areaColor: '#0082ff',
          borderColor: 'rgb(45,48,96)',
        }
      },
    },
    series: [
      {
        type: 'effectScatter',
        coordinateSystem: 'geo',
        zlevel: 2,
        labelLayout: {
          hideOverlap: true,
        },
        rippleEffect: {
          //涟漪特效
          period: 4, //动画时间，值越小速度越快
          brushType: 'stroke', //波纹绘制方式 stroke, fill
          scale: 2, //波纹圆环最大限制，值越大波纹越大
          color: '#6600ff',
        },
        label: {
          normal:{
            show: true,
            top: 'center',
            left: 'center',
            formatter: function (params) {
              //圆环显示文字
              return params.data.num
            },
            fontSize: 13,
          },
          emphasis: {
            show: true,
            position: 'right',
            formatter: function (params) {
              //圆环显示文字
              return params.data.name + '-' + params.data.num
            },
          }
        },
        symbol: 'circle',
        itemStyle: {
          normal: {
            show: true,
            color: '#fce182',
            borderWidth: 5,
            borderColor: '#6600ff'
          },
        },
        data: CenterTopData.value.world.map(function (dataItem) {
          return {
            name: dataItem.name,
            num: dataItem.value,
            value: [ dataItem.lng > -30 ? dataItem.lng - 180 : dataItem.lng + 180, dataItem.lat],
            symbolSize: dataItem.value > 100 ? 40 : dataItem.value > 50 ? 30 : 20,
          }
        })
      },
    ],
  }
  CenterTopChart.setOption(option)
}

// 全国地图绘制函数
const drawChinaMap = () => {
  CenterTopChart.clear()
  let option = {
    tooltip: {
      trigger: 'item',
      formatter: '{b} - {c}'
    },
    visualMap: {
      left: 'left',
      min: 0,
      max: 1000,
      inRange: {
        color: [
          '#74add1',
          '#4575b4',
          '#313695',
        ]
      },
      text: ['1000', '0'],
      textStyle: {
        color: '#fff'
      },
    },
    series: [
      {
        type: 'map',
        map: 'china',
        roam: true,
        zoom: 1.2,
        label: {
          emphasis: {
            show: false
          }
        },
        data: CenterTopData.value.china
      },
    ]
    // geo: {
    //   map: 'china',
    //   zoom: 1.2,
    //   label: {
    //     emphasis: {
    //       show: false
    //     }
    //   },
    //   roam: true, // 是否开启鼠标缩放和平移漫游
    //   // 地图的背景色
    //   itemStyle: {
    //     normal: {
    //       areaColor: '#3a8dcb',
    //       borderColor: '#00ffe1',
    //     }
    //   }
    // },

  }
  CenterTopChart.setOption(option)
}

// 初始化函数
const initCenterTop = () => {
  // 初始化图表
  if (CenterTopChart != null && CenterTopChart != "" && CenterTopChart != undefined) {
    CenterTopChart.dispose();//销毁
  }
  let chartDom = document.getElementById("CenterTopChart")
  CenterTopChart = echarts.init(chartDom)

  // 绘制全球地图
  drawWorldMap()
}


// 中下
const judgeType = ref('误报')

const thingLevel = ref('高')

let tableData = reactive({
  timer: 0,
  container: document.getElementById("tableItemContainer"),
  allData: {
    world: [],
    china: []
  },
  showData: [],
  itemIndex: 0,
})

const initTableData = () => {
  tableData.showData = tableData.allData.world.filter(item => item.eventLevel === thingLevel.value && item.eventJudge === judgeType.value)

  tableData.container = document.getElementById("tableItemContainer")

  tableData.timer = setInterval(function (){
    if (tableData.itemIndex >= tableData.showData.length - 1){
      tableData.container.style.top = '0px'
      tableData.itemIndex = 0
    }
    else {
      let move = tableData.container.offsetTop - 15;
      tableData.container.style.top = move + 'px'
      tableData.itemIndex++
    }
  }, 1000)

  tableData.container.onmouseover = function () {
    clearInterval(tableData.timer)
  }

  tableData.container.onmouseout = function () {
    tableData.timer = setInterval(function (){
      if (tableData.itemIndex === tableData.showData.length - 1){
        tableData.container.style.top = '0px'
        tableData.itemIndex = 0
      }
      else {
        let move = tableData.container.offsetTop - 30;
        tableData.container.style.top = move + 'px'
        tableData.itemIndex++
      }
    }, 1000)
  }
}

watch(judgeType, (NewVal) => {
  if(selectPlace.value === '全球'){
    tableData.showData = tableData.allData.world.filter(item => item.eventLevel === thingLevel.value && item.eventJudge === NewVal)
  }
  else{
    tableData.showData = tableData.allData.china.filter(item => item.eventLevel === thingLevel.value && item.eventJudge === NewVal)
  }
})

watch(thingLevel, (NewVal) => {
  if(selectPlace.value === '全球'){
    tableData.showData = tableData.allData.world.filter(item => item.eventLevel === NewVal && item.eventJudge === judgeType.value)
  }
  else{
    tableData.showData = tableData.allData.china.filter(item => item.eventLevel === NewVal && item.eventJudge === judgeType.value)
  }
})

// 右上
const staticType = ref('类型')

const thingsData = reactive({
  world: {
    levelData: [],
    typeData: [],
  },
  china: {
    levelData: [],
    typeData: [],
  },
})

let RightTopChart = null

const setRightTopChartData = () => {
  let data = []
  if(selectPlace.value === '全球'){
    if(staticType.value === '类型'){
      data = [
        { value: thingsData.world.typeData[0].num, name: thingsData.world.typeData[0].event_type },
        { value: thingsData.world.typeData[1].num, name: thingsData.world.typeData[1].event_type },
        { value: thingsData.world.typeData[2].num, name: thingsData.world.typeData[2].event_type },
        { value: thingsData.world.typeData[3].num, name: thingsData.world.typeData[3].event_type },
        {
          // make an record to fill the bottom 50%
          value: thingsData.world.typeData[0].num + thingsData.world.typeData[1].num + thingsData.world.typeData[2].num + thingsData.world.typeData[3].num,
          itemStyle: {
            // stop the chart from rendering this piece
            color: 'none',
            decal: {
              symbol: 'none'
            }
          },
          label: {
            show: false
          }
        }
      ]
    }
    else{
      data = [
        { value: thingsData.world.levelData[0].num, name: thingsData.world.levelData[0].level },
        { value: thingsData.world.levelData[1].num, name: thingsData.world.levelData[1].level },
        { value: thingsData.world.levelData[2].num, name: thingsData.world.levelData[2].level },
        {
          // make an record to fill the bottom 50%
          value: thingsData.world.levelData[0].num + thingsData.world.levelData[1].num + thingsData.world.levelData[2].num,
          itemStyle: {
            // stop the chart from rendering this piece
            color: 'none',
            decal: {
              symbol: 'none'
            }
          },
          label: {
            show: false
          }
        }
      ]
    }
  }
  else{
    if(staticType.value === '类型'){
      data = [
        { value: thingsData.china.typeData[0].num, name: thingsData.china.typeData[0].event_type },
        { value: thingsData.china.typeData[1].num, name: thingsData.china.typeData[1].event_type },
        { value: thingsData.china.typeData[2].num, name: thingsData.china.typeData[2].event_type },
        { value: thingsData.china.typeData[3].num, name: thingsData.china.typeData[3].event_type },
        {
          // make an record to fill the bottom 50%
          value: thingsData.china.typeData[0].num + thingsData.china.typeData[1].num + thingsData.china.typeData[2].num + thingsData.china.typeData[3].num,
          itemStyle: {
            // stop the chart from rendering this piece
            color: 'none',
            decal: {
              symbol: 'none'
            }
          },
          label: {
            show: false
          }
        }
      ]
    }
    else{
      data = [
        { value: thingsData.china.levelData[0].num, name: thingsData.china.levelData[0].level },
        { value: thingsData.china.levelData[1].num, name: thingsData.china.levelData[1].level },
        { value: thingsData.china.levelData[2].num, name: thingsData.china.levelData[2].level },
        {
          // make an record to fill the bottom 50%
          value: thingsData.china.levelData[0].num + thingsData.china.levelData[1].num + thingsData.china.levelData[2].num,
          itemStyle: {
            // stop the chart from rendering this piece
            color: 'none',
            decal: {
              symbol: 'none'
            }
          },
          label: {
            show: false
          }
        }
      ]
    }
  }
  return data
}

const drawRightTopChart = () => {
  if (RightTopChart != null && RightTopChart != "" && RightTopChart != undefined) {
    RightTopChart.dispose();//销毁
  }
  let chartDom = document.getElementById("RightTopChart")
  RightTopChart = echarts.init(chartDom)
  let option = {
    tooltip: {
      trigger: 'item'
    },
    series: [
      {
        name: 'Access From',
        type: 'pie',
        radius: ['50%', '100%'],
        center: ['50%', '75%'],
        // adjust the start angle
        startAngle: 180,
        label: {
          show: true,
          formatter(param) {
            // correct the percentage
            return param.name + ' (' + param.percent! * 2 + '%)';
          }
        },
        data: setRightTopChartData()
      }
    ]
  }
  option && RightTopChart.setOption(option);
}

watch(staticType, () => {
  RightTopChart.clear()
  drawRightTopChart()
})

// 右中
const RightCenterTitle = ref(2)

const chosenRightCenterTitle = (value) => {
  RightCenterTitle.value=value
}

const RightCenterData = reactive({
  world: {
    AsData: [],
    IPData: [],
  },
  china: {
    AsData: [],
    IPData: [],
  },
  showData: [],
  colorData: [
    '#0082ff',
    '#0096ff',
    '#00aaff',
    '#00beff',
    '#00d2ff',
    '#00e6ff',
    '#00faff',
  ],
})

const initRightCenterData = () => {
  if(RightCenterData.world.AsData.length > 6)
    RightCenterData.showData = RightCenterData.world.AsData.slice(0,6)
  else
    RightCenterData.showData = RightCenterData.world.AsData
}

watch(RightCenterTitle, (NewVal) => {
  if(NewVal === 1){
    if(selectPlace.value === '全球'){
      if(RightCenterData.world.IPData.length > 6)
        RightCenterData.showData = RightCenterData.world.IPData.slice(0,6)
      else
        RightCenterData.showData = RightCenterData.world.IPData
    }
    else{
      if(RightCenterData.china.IPData.length > 6)
        RightCenterData.showData = RightCenterData.china.IPData.slice(0,6)
      else
        RightCenterData.showData = RightCenterData.china.IPData
    }
  }
  else{
    if(selectPlace.value === '全球'){
      if(RightCenterData.world.AsData.length > 6)
        RightCenterData.showData = RightCenterData.world.AsData.slice(0,6)
      else
        RightCenterData.showData = RightCenterData.world.AsData
    }
    else{
      if(RightCenterData.china.AsData.length > 6)
        RightCenterData.showData = RightCenterData.china.AsData.slice(0,6)
      else
        RightCenterData.showData = RightCenterData.china.AsData
    }
  }
})

// 右下
const RightBottomTitle = ref('长时')

let RightBottomData = reactive({
  worldData: {
    longTimeData: [],
    shortTimeData: []
  },
  chinaData: {
    longTimeData: [],
    shortTimeData: []
  },
})

const initRightBottomData = () => {
  RightBottomData.worldData.longTimeData = LongTimeData.map(item => ({
    flag: item.country.flag,
    country: item.country.name,
    organization: item.org,
    as: item.asn,
    num: item.score,
  }))
  if (RightBottomData.worldData.longTimeData.length > 6)
    RightBottomData.worldData.longTimeData = RightBottomData.worldData.longTimeData.slice(0,6)

  RightBottomData.worldData.shortTimeData = ShortTimeData.map(item => ({
    flag: item.country.flag,
    country: item.country.name,
    organization: item.org,
    as: item.asn,
    num: item.score,
  }))
  if (RightBottomData.worldData.shortTimeData.length > 6)
    RightBottomData.worldData.shortTimeData = RightBottomData.worldData.shortTimeData.slice(0,6)

  RightBottomData.chinaData.longTimeData = LongTimeData.filter(item => { return item.country.name === "中国" }).map(item => ({
    flag: item.country.flag,
    country: item.country.name,
    organization: item.org,
    as: item.asn,
    num: item.score,
  }))
  if (RightBottomData.chinaData.longTimeData.length > 6)
    RightBottomData.chinaData.longTimeData = RightBottomData.chinaData.longTimeData.slice(0,6)

  RightBottomData.chinaData.shortTimeData = ShortTimeData.filter(item => { return item.country.name === "中国" }).map(item => ({
    flag: item.country.flag,
    country: item.country.name,
    organization: item.org,
    as: item.asn,
    num: item.score,
  }))
  if (RightBottomData.chinaData.shortTimeData.length > 6)
    RightBottomData.chinaData.shortTimeData = RightBottomData.chinaData.shortTimeData.slice(0,6)
}

const JumpToDetail = (url, param) => {
  const jumpurl = router.resolve({path: url,query: { msg: param }})
  window.open(jumpurl.href)
}

// 大屏切换
const SwitchBigScreen = () => {
  if (!screenfull.isEnabled) {
    ElMessage.warning('暂不不支持全屏');
    return false;
  }
  screenfull.toggle();
  screenfull.on('change', () => {
    setTimeout(() => {
      LeftTopChart.resize()
      LeftBottomChart.resize()
      CenterTopChart.resize()
      RightTopChart.resize()
    }, 200);
    if (screenfull.isFullscreen)
      useThemeConfig().updateFullScreen(true)
    else
      useThemeConfig().updateFullScreen(false)
  });
}

// 折叠切换
const ExpandOrFoldCollapse = async () => {
  themeConfig.value.isCollapse = !themeConfig.value.isCollapse;
  setTimeout(() => {
    LeftTopChart.resize()
    LeftBottomChart.resize()
    CenterTopChart.resize()
    RightTopChart.resize()
  }, 200);
}

// 监听
watch(selectPlace, (NewVal) => {
  if(NewVal === '全球') {
    // 更新左上
    LeftTopData.showData = LeftTopData.world
    LeftTopChart.clear()
    if(LeftTopTitle.value === 1) {
      let option = {
        grid: {
          top: '3%',
          left: '2%',
          right: '3%',
          bottom: '2%',
          containLabel: true
        },
        series: [
          {
            type: 'gauge',
            startAngle: 180,
            endAngle: 0,
            center: ['50%', '80%'],
            radius: '100%',
            min: 0,
            max: 1,
            splitNumber: 8,
            axisLine: {
              lineStyle: {
                width: 5,
                color: [
                  [0.3, '#FF6E76'],
                  [0.7, '#FDDD60'],
                  [1, '#7CFFB2']
                ]
              }
            },
            pointer: {
              icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
              length: '10%',
              width: 12,
              offsetCenter: [0, '-55%'],
              itemStyle: {
                color: 'auto'
              }
            },
            axisTick: {
              length: 12,
              lineStyle: {
                color: 'auto',
                width: 1
              }
            },
            splitLine: {
              length: 12,
              lineStyle: {
                color: 'auto',
                width: 2
              }
            },
            axisLabel: {
              color: '#ffffff',
              fontSize: 15,
              distance: -35,
              rotate: 'tangential',
              formatter: function (value) {
                if (value === 0.875) {
                  return '优秀';
                } else if (value === 0.5) {
                  return '良好';
                } else if (value === 0.125) {
                  return '一般';
                }
                return '';
              }
            },
            title: {
              color: '#ffffff',
              offsetCenter: [0, '-10%'],
              fontSize: 12
            },
            detail: {
              fontSize: 24,
              offsetCenter: [0, '-35%'],
              valueAnimation: true,
              formatter: function (value) {
                return Math.round(value * 100) + '';
              },
              color: 'inherit'
            },
            data: [
              {
                value: (LeftTopData.showData.num/100.0).toFixed(2),
                name: '安全态势系数'
              }
            ]
          }
        ]
      }
      LeftTopChart.setOption(option);
    }
    else if(LeftTopTitle.value === 2) {
      let option = {
        title: {
          top: "center",
          left: "center",
          text: LeftTopData.showData.allStatic.toString() + '\n' + '总数',
          textStyle: {
            color: "#ffffff",
            lineHeight: 20,
            fontSize: 14,
            padding:[14,0,0,0]
          },
        },
        tooltip: {
          trigger: 'item'
        },
        series: {
          type: 'sunburst',
          data: LeftTopData.showData.static,
          radius: ['40%', '95%'],
          center: ['50%', '50%'],
          itemStyle: {
            borderRadius: 10,
            borderWidth: 2
          },
          label: {
            show: true,
            rotate: 'radial',
            color: 'white',
            fontSize: 10,
          },
        }
      }
      LeftTopChart.setOption(option);
    }

    // 更新左下
    drawLeftBottomChart()

    // 更新中间地图
    drawWorldMap()

    // 更新中间列表
    tableData.itemIndex = 0
    tableData.container.style.top = '0px'
    tableData.showData = tableData.allData.world.filter(item => item.eventLevel === thingLevel.value)
  }
  else {
    // 更新左上
    LeftTopData.showData = LeftTopData.china
    LeftTopChart.clear()
    if(LeftTopTitle.value === 1) {
      let option = {
        grid: {
          top: '3%',
          left: '2%',
          right: '3%',
          bottom: '2%',
          containLabel: true
        },
        series: [
          {
            type: 'gauge',
            startAngle: 180,
            endAngle: 0,
            center: ['50%', '80%'],
            radius: '100%',
            min: 0,
            max: 1,
            splitNumber: 8,
            axisLine: {
              lineStyle: {
                width: 5,
                color: [
                  [0.3, '#FF6E76'],
                  [0.7, '#FDDD60'],
                  [1, '#7CFFB2']
                ]
              }
            },
            pointer: {
              icon: 'path://M12.8,0.7l12,40.1H0.7L12.8,0.7z',
              length: '10%',
              width: 12,
              offsetCenter: [0, '-55%'],
              itemStyle: {
                color: 'auto'
              }
            },
            axisTick: {
              length: 12,
              lineStyle: {
                color: 'auto',
                width: 1
              }
            },
            splitLine: {
              length: 12,
              lineStyle: {
                color: 'auto',
                width: 2
              }
            },
            axisLabel: {
              color: '#ffffff',
              fontSize: 15,
              distance: -35,
              rotate: 'tangential',
              formatter: function (value) {
                if (value === 0.875) {
                  return '优秀';
                } else if (value === 0.5) {
                  return '良好';
                } else if (value === 0.125) {
                  return '一般';
                }
                return '';
              }
            },
            title: {
              color: '#ffffff',
              offsetCenter: [0, '-10%'],
              fontSize: 12
            },
            detail: {
              fontSize: 24,
              offsetCenter: [0, '-35%'],
              valueAnimation: true,
              formatter: function (value) {
                return Math.round(value * 100) + '';
              },
              color: 'inherit'
            },
            data: [
              {
                value: (LeftTopData.showData.num/100.0).toFixed(2),
                name: '安全态势系数'
              }
            ]
          }
        ]
      }
      LeftTopChart.setOption(option);
    }
    else if(LeftTopTitle.value === 2) {
      let option = {
        title: {
          top: "center",
          left: "center",
          text: LeftTopData.showData.allStatic.toString() + '\n' + '总数',
          textStyle: {
            color: "#ffffff",
            lineHeight: 20,
            fontSize: 14,
            padding:[14,0,0,0]
          },
        },
        tooltip: {
          trigger: 'item'
        },
        series: {
          type: 'sunburst',
          data: LeftTopData.showData.static,
          radius: ['40%', '95%'],
          center: ['50%', '50%'],
          itemStyle: {
            borderRadius: 10,
            borderWidth: 2
          },
          label: {
            show: true,
            rotate: 'radial',
            color: 'white',
            fontSize: 10,
          },
        }
      }
      LeftTopChart.setOption(option);
    }

    // 更新左下
    drawLeftBottomChart()

    // 更新中间地图
    drawChinaMap()

    // 更新中间列表
    tableData.itemIndex = 0
    tableData.container.style.top = '0px'
    tableData.showData = tableData.allData.china.filter(item => item.eventLevel === thingLevel.value)
  }
})


// 数据初始化函数
const initData = async () => {
  // 修正：直接请求数据接口，移除多余的下载步骤
  const AllData = await request({
    url: '/dashboard/screens/security',
    method: 'get',
    data: {},
  });

  console.log(AllData)

  // 左上数据
  LeftTopData.china = AllData.LeftTopData.china
  LeftTopData.world = AllData.LeftTopData.world
  initLeftTop()

  // 左中数据
  LeftCenterData.value = AllData.LeftCenterData
  if(LeftCenterData.value.china.asData.length > 6)
    LeftCenterData.value.china.asData = LeftCenterData.value.china.asData.slice(0,6)
  if(LeftCenterData.value.china.organizationData.length > 6)
    LeftCenterData.value.china.organizationData = LeftCenterData.value.china.organizationData.slice(0,6)
  if(LeftCenterData.value.world.asData.length > 6)
    LeftCenterData.value.world.asData = LeftCenterData.value.world.asData.slice(0,6)
  if(LeftCenterData.value.world.organizationData.length > 6)
    LeftCenterData.value.world.organizationData = LeftCenterData.value.world.organizationData.slice(0,6)


  // 左下数据
  LeftBottomData.value = AllData.LeftBottomData
  drawLeftBottomChart()

  // 中上数据
  CenterTopData.value.world = AllData.CenterTopData.world
  CenterTopData.value.china = AllData.CenterTopData.china.map((item) => ({
    name: item.city,
    value: item.value
  }))
  initCenterTop()

  // 中下数据
  tableData.allData.world = AllData.CenterBottomData.world.map((item) => ({
    attackedAS: item.attackedAS,
    attackedCountry: item.attackedCountry,
    attackedFlag: item.attackedFlag,
    attackedOrg: item.attackedOrg,
    attackerAS: item.attackerAS,
    attackerCountry: item.attackerCountry,
    attackerFlag: item.attackerFlag,
    attackerOrg: item.attackerOrg,
    endTime: item.endTime,
    eventJudge: item.eventJudge? '异常': '误报',
    eventLevel: item.eventLevel === 'high'? '高' :(item.eventLevel === 'middle'? '中': '低' ),
    eventType: item.eventType,
    startTime: item.startTime,
  }))
  tableData.allData.china = AllData.CenterBottomData.china.map((item) => ({
    attackedAS: item.attackedAS,
    attackedCountry: item.attackedCountry,
    attackedFlag: item.attackedFlag,
    attackedOrg: item.attackedOrg,
    attackerAS: item.attackerAS,
    attackerCountry: item.attackerCountry,
    attackerFlag: item.attackerFlag,
    attackerOrg: item.attackerOrg,
    endTime: item.endTime,
    eventJudge: item.eventJudge? '异常': '误报',
    eventLevel: item.eventLevel === 'high'? '高' :(item.eventLevel === 'middle'? '中': '低' ),
    eventType: item.eventType,
    startTime: item.startTime,
  }))
  initTableData()

  // 右上数据
  thingsData.world.typeData = AllData.RightTopData.world.typeData.filter(item => item.event_type === "前缀劫持" || item.event_type === "路由泄漏" || item.event_type === "AS中断" || item.event_type === "国家中断")
  thingsData.world.levelData = AllData.RightTopData.world.levelData.map((item) => ({
    amplitude: item.amplitude,
    amplitude_type: item.amplitude_type,
    level: item.level === 'high'? '高危事件': item.level === 'middle'? '中危事件': '低危事件',
    num: item.num
  }))
  thingsData.china.typeData = AllData.RightTopData.china.typeData.filter(item => item.event_type === "前缀劫持" || item.event_type === "路由泄漏" || item.event_type === "AS中断" || item.event_type === "国家中断")
  thingsData.china.levelData = AllData.RightTopData.china.levelData.map((item) => ({
    amplitude: item.amplitude,
    amplitude_type: item.amplitude_type,
    level: item.level === 'high'? '高危事件': item.level === 'middle'? '中危事件': '低危事件',
    num: item.num
  }))
  drawRightTopChart()

  // 右中数据
  RightCenterData.world = AllData.RightCenterData.world
  RightCenterData.china = AllData.RightCenterData.china

  if(RightCenterData.china.AsData.length > 6)
    RightCenterData.china.AsData = RightCenterData.china.AsData.slice(0,6)
  if(RightCenterData.china.IPData.length > 6)
    RightCenterData.china.IPData = RightCenterData.china.IPData.slice(0,6)
  if(RightCenterData.world.AsData.length > 6)
    RightCenterData.world.AsData = RightCenterData.world.AsData.slice(0,6)
  if(RightCenterData.world.IPData.length > 6)
    RightCenterData.world.IPData = RightCenterData.world.IPData.slice(0,6)

  initRightCenterData()

  // 右下数据
  initRightBottomData()
}

onMounted(async () => {

  echarts.registerMap('world',  worldMapRemix)
  echarts.registerMap('china',  cn_map)

  await initData()

})

</script>

<style scoped lang="scss">
.index{
  width: 100%;
  height: 100%;
  overflow: hidden;
  background: url('../../../assets/img.png');
  background-size: 100% 100%;
  text-overflow: ellipsis;
  white-space: nowrap;

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
      color: white;
      font-size: small;
      font-weight: bold;
      justify-content: flex-end;
      display: flex;
      align-items: center;

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

      :deep(.el-radio){
        color: white;
      }
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

  :deep(.el-dialog){
    width: 30%;
    //background: #37b2ff;
    border-radius: 10px;
  }
}
</style>