<template>
  <div class="CreditDetailContainer">
    <div class="CreditDetailTitleContainer">自治域信誉详情信息</div>

    <el-row class="CreditDetailMiddleContainer" align="middle" justify="space-around">
      <el-col :span="4" style="width: 100%;height: 100%;">
        <div class="ASScoreTitleContainer">自治域信誉分数</div>
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
  </div>
</template>

<script setup lang="ts">
import * as echarts from "echarts";
import {onMounted, reactive, ref} from "vue";
import {TableColumnCtx} from "element-plus";
import AsCreditData from '/@/assets/credit/as_credit.json'
import {useRouter} from "vue-router";

const router = useRouter()

let creditData = reactive({})

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

const tableData = ref([])

const initCreditData = async () => {
  console.log(router.currentRoute.value.query.msg)
  creditData = await AsCreditData.filter(item => item.as === router.currentRoute.value.query.msg)[0]
  tableData.value = [
    {
      prop1: 'AS号',
      prop2: '',
      prop3: '',
      prop4: creditData.as,
      prop5: '',
      prop6: ''
    },
    {
      prop1: '国家',
      prop2: creditData.country,
      prop3: '机构',
      prop4: creditData.org,
      prop5: 'AS排行',
      prop6: creditData.asRank
    },
    {
      prop1: '最近15天低等级事件频次',
      prop2: creditData.fifteenLowTimes,
      prop3: '最近15天中等级事件频次',
      prop4: creditData.fifteenMiddleTimes,
      prop5: '最近15天高等级事件频次',
      prop6: creditData.fifteenHighTimes
    },
    {
      prop1: '历史低等级事件频次',
      prop2: creditData.totalLowTimes,
      prop3: '历史中等级事件频次',
      prop4: creditData.totalMiddleTimes,
      prop5: '历史高等级事件频次',
      prop6: creditData.totalHighTimes
    },
    {
      prop1: '最近15天异常事件频次',
      prop2: creditData.fifteenTimes,
      prop3: '历史异常事件频次',
      prop4: creditData.totalTimes,
      prop5: '',
      prop6: ''
    },
  ]
  console.log(creditData)
}

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
            value: creditData.score.toFixed(2)
          }
        ]
      }
    ]
  };
  option && ScoreChart.setOption(option);
}

onMounted(async () => {
  await initCreditData()
  drawScoreChart()
})

</script>

<style lang="scss" scoped>
.CreditDetailContainer{
  width: calc(100% - 40px);
  height: calc(100% - 40px);
  padding: 20px;
  background-color: white;

  .CreditDetailTitleContainer{
    width: 100%;
    height: 60px;
    text-align: center;
    font-size: xx-large;
    font-weight: bolder;
    line-height: 60px;
  }

  .CreditDetailMiddleContainer{
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
}
</style>