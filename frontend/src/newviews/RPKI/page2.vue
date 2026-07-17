<template>
  <div class="PageContainer">
    <div class="SearchContainer">
      <div class="TitleContainer">Trust Anchor</div>
      <div class="SelectContainer">
        <el-select
            v-model="Anchor"
            placeholder="Select"
            style="width: 240px"
        >
          <el-option
              v-for="item in AnchorList"
              :key="item.value"
              :label="item.label"
              :value="item.value"
          />
        </el-select>
      </div>
    </div>
    <div class="TableContainer">
      <el-table :data="tableData" v-loading="loading" border stripe>
        <el-table-column type="index" label="序号" :index="indexMethod" width="100" style="text-align: center;" />
        <el-table-column prop="notBefore" label="notBefore" />
        <el-table-column prop="notAfter" label="notAfter" />
        <el-table-column prop="subject" label="subject" />
        <el-table-column prop="issuer" label="issuer" />
        <el-table-column prop="cerIpAddresses" label="cerIpAddresses"/>
        <el-table-column prop="asns" label="asns"/>
        <el-table-column prop="origin" label="origin"/>
      </el-table>
    </div>
    <div class="PageContainer">
      <el-pagination
          v-model:current-page="pageData.currentPage"
          v-model:page-size="pageData.pageSize"
          layout="total, sizes, prev, pager, next, jumper"
          :total="total"
      />
    </div>
  </div>
</template>

<script setup lang="ts">
import {onMounted, reactive, ref, watch} from "vue";
import request from "/@/utils/request";

const indexMethod = (index: number) => {
  return (pageData.currentPage - 1)*pageData.pageSize + index+1
}

const AnchorList = ref([
  {
    value: 'AFRINIC',
    label: 'AFRINIC',
  },
  {
    value: 'RIPE NCC',
    label: 'RIPE NCC',
  },
  {
    value: 'ARIN',
    label: 'ARIN',
  },
  {
    value: 'LACNIC',
    label: 'LACNIC',
  },
  {
    value: 'APNIC',
    label: 'APNIC',
  },
])

const Anchor = ref(AnchorList.value[0].label)

const loading = ref(false)

const total = ref(40000)

let pageData = reactive({
  currentPage: 1,
  pageSize: 10,
})

const tableData = ref([])

const updataTableData = async () => {
  loading.value = true
  let res = await request({
    url: 'http://10.3.242.224:8070/sys/exportcers',
    // url: 'http://your-rpki-host/sys/exportcers',
    headers: {
      "Content-Type": "application/json",
    },
    method: 'post',
    data: JSON.stringify({
      "rir": Anchor.value,
      "page": pageData.currentPage,
      "pageSize": pageData.pageSize
    }),
  })
  total.value = res.count
  tableData.value = res.data
  loading.value = false
}

watch(Anchor, ()=>{
  pageData = {
    currentPage: 1,
    pageSize: 10,
  }
  updataTableData()
})

watch(pageData, ()=>{
  updataTableData()
})

onMounted(()=>{
  updataTableData()
})

</script>

<style scoped lang="scss">
.PageContainer{
  width: calc(100% - 40px);
  height: calc(100% - 40px);
  padding: 20px;

  .SearchContainer{
    height: 60px;

    .TitleContainer{
      width: auto;
      height: 15px;
      font-size: 12px;
      line-height: 15px;
      color: grey;
    }

    .SelectContainer{
      width: auto;
      height: 45px;
    }
  }

  .TableContainer{
    width: 100%;
    max-height: 800px;
    overflow-y: scroll;
  }

  .PageContainer{
    width: 100%;
    margin-top: 20px;
  }
}
</style>
