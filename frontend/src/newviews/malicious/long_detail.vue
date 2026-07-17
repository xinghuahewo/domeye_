<template>
  <div class="ShortDetailContainer">
    <div class="ShortDetailTitleContainer">自治系统长时恶意详情信息</div>

    <el-row class="ShortDetailMiddleContainer" align="middle" justify="space-around">
      <el-col :span="4" style="width: 100%;height: 100%;">
        <div class="ASScoreTitleContainer">长时AS恶意分数</div>
        <div class="ASScoreContainer" id="ASScoreContainer"></div>
      </el-col>
      <el-col :span="18" style="width: 100%;" class="ASInfoTable">
        <el-table :data="tableData" border :span-method="arraySpanMethod" :show-header="false" :cell-style="cellStyle">
          <el-table-column align="center" prop="prop1" label="" />
          <el-table-column align="center" prop="prop2" label="">
            <template #default="scope">
              <template v-if="scope.$index === 1">
                <span class="flag-icon" :class="scope.row.prop2.flag"></span>
                <span>{{'   ' + scope.row.prop2.name}}</span>
              </template>
              <template v-else>
                <span>{{ scope.row.prop2 }}</span>
              </template>
            </template>
          </el-table-column>
          <el-table-column align="center" prop="prop3" label="" />
          <el-table-column align="center" prop="prop4" label="" />
          <el-table-column align="center" prop="prop5" label="" />
          <el-table-column align="center" prop="prop6" label="" />
        </el-table>
      </el-col>
    </el-row>

    <div class="ShortDetailTabContainer">
      <el-tabs v-model="activeName" class="demo-tabs">
        <el-tab-pane name="前缀劫持">
          <template #label>
            <span class="custom-tabs-label">
              <el-icon><Operation /></el-icon>
              <span>前缀劫持</span>
            </span>
          </template>
          <el-table :data="AllData.hijack_events" height="450" border stripe>
            <el-table-column align="center" type="index" label="序号" :index="indexMethod" width="100" style="text-align: center;" />
            <el-table-column align="center" prop="victim_country" label="国家">
              <template #default="scope">
                <span class="flag-icon" :class="scope.row.victim_country.flag"></span>
                <span>{{'   ' + scope.row.victim_country.name}}</span>
              </template>
            </el-table-column>
            <el-table-column align="center" prop="victim_org" label="机构" />
            <el-table-column align="center" prop="victim" label="受影响AS号" />
            <el-table-column align="center" prop="prefix" label="被劫持前缀" />
            <el-table-column align="center" prop="begin_time" label="开始时间" />
            <el-table-column align="center" prop="end_time" label="结束时间" />
          </el-table>
        </el-tab-pane>
      </el-tabs>
    </div>
  </div>
</template>

<script setup lang="ts">
import {useRouter} from "vue-router";
import LongTimeData from '/@/assets/Malicious/blacklist_long.json'
import * as echarts from "echarts";
import {ref, onMounted, reactive} from "vue";
import { Operation } from "@element-plus/icons-vue";
import type { TableColumnCtx } from 'element-plus'

const router = useRouter()

let AllData = LongTimeData.filter(item => item.asn === router.currentRoute.value.query.msg)[0]

interface TableType {
  prop1: string,
  prop2: string | Object,
  prop3: string,
  prop4: string,
  prop5: string,
  prop6: string
}

interface SpanMethodProps {
  row: TableType
  column: TableColumnCtx<TableType>
  rowIndex: number
  columnIndex: number
}

const arraySpanMethod = ({
                           row,
                           column,
                           rowIndex,
                           columnIndex,
                         }: SpanMethodProps) => {
  if (rowIndex === 0) {
    if (columnIndex === 0) {
      return [1, 3]
    } else if (columnIndex === 3) {
      return [1, 3]
    } else{
      return [0, 0]
    }
  }
}

const cellStyle = ({ row, column, rowIndex, columnIndex }) => {
  if(columnIndex % 2 === 0){
    return { fontWeight: 'border', backgroundColor: 'rgba(237,240,252,0.4)' };
  }
}

const tableData = reactive([
  {
    prop1: 'AS号',
    prop2: '',
    prop3: '',
    prop4: AllData.asn,
    prop5: '',
    prop6: ''
  },
  {
    prop1: '国家',
    prop2: AllData.country,
    prop3: '机构',
    prop4: AllData.org,
    prop5: '网络连接度',
    prop6: AllData.degree
  },
  {
    prop1: '消失次数',
    prop2: AllData.drop_counts,
    prop3: '最近15天劫持次数',
    prop4: AllData.new_hijack_num,
    prop5: '历史总劫持次数',
    prop6: AllData.history_hijack_num
  },
  {
    prop1: '具有劫持行为的前缀占比',
    prop2: AllData.hijack_ratio,
    prop3: '服务提供商稳定性',
    prop4: AllData.provider_stability,
    prop5: '前缀集合稳定性',
    prop6: AllData.prefix_num_volatility
  },
  {
    prop1: '宣告稳定性',
    prop2: AllData.announce_volatility,
    prop3: '撤回稳定性',
    prop4: AllData.withdraw_volatility,
    prop5: '前缀平均时长',
    prop6: AllData.prefix_lifespan
  },
  {
    prop1: '16位前缀集合相似程度',
    prop2: AllData.prefix_lifespan,
    prop3: '20位前缀集合相似程度',
    prop4: AllData.prefix_num_volatility,
    prop5: '24位前缀集合相似程度',
    prop6: AllData.prefix_stability
  },
])

const activeName = ref('前缀劫持')

const drawScoreChart  = () => {
  let chartDom = document.getElementById('ASScoreContainer');
  let ScoreChart = echarts.init(chartDom);
  let option = {
    series: [
      {
        type: 'gauge',
        axisLine: {
          lineStyle: {
            width: 10,
            color: [
              [0.3, '#8ae367'],
              [0.7, '#ffea00'],
              [1, '#d41a1a']
            ]
          }
        },
        pointer: {
          itemStyle: {
            color: 'auto'
          }
        },
        axisTick: {
          distance: -30,
          length: 8,
          lineStyle: {
            color: '#fff',
            width: 2
          }
        },
        splitLine: {
          distance: -50,
          length: 30,
          lineStyle: {
            color: '#fff',
            width: 4
          }
        },
        axisLabel: {
          color: 'inherit',
          distance: 40,
          fontSize: 12
        },
        detail: {
          valueAnimation: true,
          formatter: '{value}',
          fontSize: 20,
          color: 'inherit'
        },
        data: [
          {
            value: AllData.score.toFixed( 2 )
          }
        ]
      }
    ]
  };
  option && ScoreChart.setOption(option);
}

const indexMethod = (index: number) => {
  return index+1
}

onMounted(() => {
  AllData = LongTimeData.filter(item => item.asn === router.currentRoute.value.query.msg)[0]
  drawScoreChart()
})

</script>

<style lang="scss" scoped>
.ShortDetailContainer{
  width: calc(100% - 40px);
  height: calc(100% - 40px);
  padding: 20px;
  background-color: white;

  .ShortDetailTitleContainer{
    width: 100%;
    height: 60px;
    text-align: center;
    font-size: xx-large;
    font-weight: bolder;
    line-height: 60px;
  }

  .ShortDetailMiddleContainer{
    width: 100%;
    height: 360px;

    .ASScoreTitleContainer{
      width: 100%;
      height: 40px;
      text-align: center;
      font-size: x-large;
      font-weight: bolder;
      line-height: 40px;
    }

    .ASScoreContainer{
      width: 100%;
      height: calc(100% - 40px);
    }

    .ASInfoTable{
      .el-col{
        width: 100%;
        height: 60px;
        border: 1px solid black
      }
    }
  }

  .ShortDetailTabContainer{
    width: 100%;
    height: 500px;
    padding-left: 5%;
    padding-right: 5%;

    .el-tabs__content {
      padding: 32px;
      color: #6b778c;
      font-size: 32px;
      font-weight: 600;
    }
    .custom-tabs-label .el-icon {
      vertical-align: middle;
    }
    .custom-tabs-label span {
      vertical-align: middle;
      margin-left: 4px;
    }

    .PrefixContainer{
      width: 100%;
      height: 450px;
    }

    .ActivateContainer{
      width: 100%;
      height: 450px;
    }

  }

}
</style>