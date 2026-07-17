<template>
  <div class="prefixInfo">
    <div class="prefixTable">
      <template v-if="type === '国家中断'">
        <el-table :data="tableData" style="margin: 0 40px;width: calc(100% - 80px)">
          <el-table-column type="index" label="序号" :index="indexMethod" width="100"/>
          <el-table-column prop="as" label="AS号" min-width="120"/>
          <el-table-column prop="as_name" label="AS名称" min-width="120"/>
          <el-table-column prop="as_org" label="所属机构" min-width="120"/>
          <el-table-column prop="as_country" label="所属国家" min-width="120"/>
        </el-table>
      </template>
      <template v-else>
        <el-table :data="tableData" style="margin: 0 40px;width: calc(100% - 80px)">
          <el-table-column type="index" label="序号" :index="indexMethod" width="100"/>
          <el-table-column prop="domain" label="网站域名" min-width="120"/>
          <el-table-column prop="domain_title" label="网站名称" min-width="130" />
          <el-table-column prop="domain_ip" label="IP地址" min-width="130" />
          <el-table-column prop="domain_industry" label="行业分类" min-width="130" />
          <el-table-column prop="domain_prefix" label="路由前缀" min-width="130" />
          <el-table-column prop="is_auth" label="重要程度" min-width="130">
            <template #default="scope">
              <span :class="{highLevel: scope.row.is_auth === true, lowLevel: scope.row.is_auth === false}">{{ scope.row.is_auth === true ? '重要' : '普通' }}</span>
            </template>
          </el-table-column>
        </el-table>
      </template>
    </div>
    <div class="pageTable">
      <el-pagination
          v-model:current-page="pageData.currentPage"
          v-model:page-size="pageData.pageSize"
          layout="total, sizes, prev, pager, next, jumper"
          :total="total"
      />
    </div>
  </div>
</template>

<script>
export default {
  name: 'PrefixInfo',
  props: ['subdata', 'type'],
  data() {
    return{
      tableData: [],
      pageData: {
        currentPage: 1,
        pageSize: 10
      },
      total: 100000,
    }
  },
  watch: {
    subdata(val) {
      if(this.type === '国家中断'){
        this.total = val.outage_as_info.length
        this.tableData = val.outage_as_info.slice((this.pageData.currentPage-1)*this.pageData.pageSize, this.pageData.currentPage*this.pageData.pageSize)
      }
      else{
        this.total = val.domain_list.length
        this.tableData = val.domain_list.slice((this.pageData.currentPage-1)*this.pageData.pageSize, this.pageData.currentPage*this.pageData.pageSize)
      }
    },
    pageData: {
      handler(newName){
        if(this.type === '国家中断'){
          this.tableData = this.$props.subdata.outage_as_info.slice((newName.currentPage-1)*newName.pageSize, newName.currentPage*newName.pageSize)
        }
        else{
          this.tableData = this.$props.subdata.domain_list.slice((newName.currentPage-1)*newName.pageSize, newName.currentPage*newName.pageSize)
        }
      },
      immediate: true,
      deep: true
    },
  },
  mounted() {
    if(this.type === '国家中断'){
      this.total = this.$props.subdata.outage_as_info.length
      this.tableData = this.$props.subdata.outage_as_info.slice((this.pageData.currentPage-1)*this.pageData.pageSize, this.pageData.currentPage*this.pageData.pageSize)
    }
    else{
      this.total = this.$props.subdata.domain_list.length
      this.tableData = this.$props.subdata.domain_list.slice((this.pageData.currentPage-1)*this.pageData.pageSize, this.pageData.currentPage*this.pageData.pageSize)
    }
  },
  methods: {
    indexMethod(index) {
      return (this.pageData.currentPage - 1)*this.pageData.pageSize + index + 1
    }
  }
}
</script>

<style lang="scss" scoped>
.prefixTable {
  padding-top: 25px;
}
.pageTable{
  padding: 20px 40px;
}
.el-table {
  margin: 0 auto;
}
</style>