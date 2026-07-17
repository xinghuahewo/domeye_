<template>
  <div class="TableContainer">
    <div style="width: 100%;max-height: 790px;overflow-y: scroll">
      <el-table :data="tableData" border stripe >
      <el-table-column type="index" label="序号" :index="indexMethod" width="100" style="text-align: center;" />
      <el-table-column
        prop="asCountry"
        filter-placement="bottom-end"
        label="国家"
      >
        <template #default="scope">
          <span class="flag-icon" :class="scope.row.asCountry.flag"></span>
          <span>{{'  ' + scope.row.asCountry.name}}</span>
        </template>
      </el-table-column>
      <el-table-column
        prop="asOrganization"
        filter-placement="bottom-end"
        label="机构"
      />
      <el-table-column prop="asNumber" label="AS号" />
      <el-table-column prop="asActivate" label="网络活跃度" sortable />
      <el-table-column prop="asDegree" label="网络连接度" sortable />
      <el-table-column prop="asScore" label="恶意分数" sortable />
      <el-table-column fixed="right" label="详情" width="140">
        <template #default="scope">
          <el-button link type="primary" size="small" @click="JumpToDetail(scope.row.asNumber)">详情</el-button>
        </template>
      </el-table-column>
    </el-table>
    </div>
    <div style="margin-top: 20px;margin-left: 20px">
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
import {useRouter} from "vue-router";
import ShortTimeData from '/@/assets/Malicious/blacklist_short.json'
import {onMounted, reactive, ref, watch} from "vue";

const pageData = reactive({
  currentPage: 1,
  pageSize: 50,
  total: ShortTimeData.length,
})

watch(pageData, () => {
  updataTableData()
})

const indexMethod = (index: number) => {
  return (pageData.currentPage - 1)*pageData.pageSize + index+1
}

const tableData = ref([])

const updataTableData = () => {
  tableData.value = ShortTimeData.slice((pageData.currentPage - 1) * pageData.pageSize, pageData.currentPage * pageData.pageSize).map((item) => ({
    asCountry: item.country,
    asOrganization: item.org,
    asNumber: item.asn,
    asActivate: item.drop_counts,
    asDegree: item.degree,
    asScore: item.score
  }))
}

const router = useRouter()
const JumpToDetail = (param) => {
  const jumpurl = router.resolve({path: '/malicious/short_detail',query: { msg: param }})
  window.open(jumpurl.href);
}

onMounted(() => {
  updataTableData()
  updataTableData()
})

</script>

<style scoped lang="scss">
.TableContainer{
  width: calc(100% - 40px);
  height: calc(100% - 40px);
  padding: 20px;

  :deep(.cell){
    text-align: center;
  }
}
</style>
