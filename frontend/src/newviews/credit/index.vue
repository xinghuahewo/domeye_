<template>
  <div class="TabelContainer">
    <div style="width: 100%;max-height: 800px;overflow-y: scroll">
      <el-table :data="tableData" border stripe >
        <el-table-column type="index" label="序号" :index="indexMethod" width="140" align="center" />
        <el-table-column
            prop="asCountry"
            filter-placement="bottom-end"
            label="归属国家"
            align="center"
        >
          <template #default="scope">
            <span class="flag-icon" :class="scope.row.asCountry.flag"></span>
            <span>{{'   ' + scope.row.asCountry.name}}</span>
          </template>
        </el-table-column>
        <el-table-column
            prop="asOrganization"
            filter-placement="bottom-end"
            label="归属机构"
            align="center"
        />
        <el-table-column prop="asNumber" label="AS号码" align="center" />
        <el-table-column prop="creditScore" label="信誉评分" sortable align="center" />
        <el-table-column fixed="right" label="评分依据" width="140" align="center" >
          <template #default="scope">
            <el-button link type="primary" size="small" @click="JumpToDetail(scope.row.asNumber)">依据详情</el-button>
          </template>
        </el-table-column>
      </el-table>
    </div>
    <div style="margin-top: 20px">
      <el-pagination
          v-model:current-page="pageData.currentPage"
          v-model:page-size="pageData.pageSize"
          layout="total, sizes, prev, pager, next, jumper"
          :total="pageData.total"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import AsCreditData from '/@/assets/credit/as_credit.json'
import {useRouter} from "vue-router";
import {onMounted, reactive, ref, watch} from "vue";

const indexMethod = (index: number) => {
  return index+1
}

const pageData = reactive({
  currentPage: 1,
  pageSize: 50,
  total: AsCreditData.length,
})

watch(pageData, () => {
  updataTableData()
})

const tableData = ref([])

const updataTableData = () => {
  tableData.value = AsCreditData.slice((pageData.currentPage - 1) * pageData.pageSize, pageData.currentPage * pageData.pageSize).map(item => ({
    asNumber: item.as,
    asCountry: item.country,
    asOrganization: item.org,
    creditScore: item.score
  }))
}

function removeDuplicate(arr) {
  let newArray = []
  arr.forEach(item => {
    if(newArray.filter(i => { return JSON.stringify(i) === JSON.stringify(item) }).length === 0)
      newArray.push(item)
  })
  return newArray
}

const filterCountryOption = () => {
  let temp = tableData.value.map((item) => ({ text: item.asCountry, value: item.asCountry }))
  return removeDuplicate(temp)
}

const filterOrganizationOption = () => {
  let temp = tableData.value.map((item) => ({ text: item.asOrganization, value: item.asOrganization }))
  return removeDuplicate(temp)
}

const router = useRouter()
const JumpToDetail = (param) => {
  router.push({path: '/credit/detail',query: { msg: param }})
}

onMounted(() => {
  updataTableData()
})

</script>

<style scoped lang="scss">
.TabelContainer{
  width: calc(100% - 40px);
  height: calc(100% - 40px);
  padding: 20px;
}
</style>
