<template>
  <div class="system-menu-container layout-pd">
    <el-card shadow="hover">
      <el-row>
        <el-col :span="3">
          <map-menu class="menu-map" @getValue="getSonMsg" :menuList="state.menulist"></map-menu>
        </el-col>
        <el-col :span="2"></el-col>
        <el-col :span="16">
          <el-table
              :data="state.tableData.data"
              v-loading="state.tableData.loading"
              style="width: 100%"
              row-key="path"
              :tree-props="{ children: 'children', hasChildren: 'hasChildren' }"
          >
            <el-table-column label="菜单名称" show-overflow-tooltip>
              <template #default="scope">
                <SvgIcon :name="scope.row.meta.icon"/>
                <span class="ml10">{{ $t(scope.row.meta.title) }}</span>
              </template>
            </el-table-column>
            <el-table-column prop="path" label="路由路径" show-overflow-tooltip></el-table-column>
          </el-table>
        </el-col>
      </el-row>

    </el-card>
    <MenuDialog ref="menuDialogRef" @refresh="getTableData()"/>
  </div>
</template>

<script setup lang="ts" name="systemMenu">
import {defineAsyncComponent, ref, onMounted, reactive} from 'vue';
import {RouteRecordRaw} from 'vue-router';
import {ElMessageBox, ElMessage} from 'element-plus';
import {storeToRefs} from 'pinia';
import {useRoutesList} from '/@/stores/routesList';
// import MapMenu from "/@/views/system/systest/mapMenu.vue";

// import { setBackEndControlRefreshRoutes } from "/@/router/backEnd";

// 引入组件
const MenuDialog = defineAsyncComponent(() => import('/@/views/system/menu/dialog.vue'));
const MapMenu = defineAsyncComponent(() => import('/@/components/menu_child/mapMenu.vue'))
// 定义变量内容
const stores = useRoutesList();
const {routesList} = storeToRefs(stores);
const menuDialogRef = ref();
const state = reactive({
  tableData: {
    data: [] as RouteRecordRaw[],
    loading: true,
  },
  menulist: [
    {
      index: 1,
      name: '全部'
    },{
    index: 2,
    name: '中国电信(5)'
  }, {
    index: 3,
    name: '中国移动(3)'
  }, {
    index: 4,
    name: '中国联通(2)'
  },{
    index: 5,
    name: 'CNIIC(15)'
  },{
    index: 6,
    name: '阿里云(1)'
  },{
    index: 7,
    name: '腾讯云(3)'
  },

  ]
});
// 定义接收子组件信息的函数
const getSonMsg = (value) => {
  console.log('sonmsg', value)
  state.tableData.data = state.tableData.data.splice(0,value*3)
};


// 获取路由数据，真实请从接口获取
const getTableData = () => {
  state.tableData.loading = true;
  state.tableData.data = routesList.value;
  console.log(state.tableData.data)
  setTimeout(() => {
    state.tableData.loading = false;
  }, 50);
};
// 打开新增菜单弹窗
const onOpenAddMenu = (type: string) => {
  menuDialogRef.value.openDialog(type);
  // console.log(menuDialogRef.value)
};
// 打开编辑菜单弹窗
const onOpenEditMenu = (type: string, row: RouteRecordRaw) => {
  menuDialogRef.value.openDialog(type, row);
};
// 删除当前行
const onTabelRowDel = (row: RouteRecordRaw) => {
  ElMessageBox.confirm(`此操作将永久删除路由：${row.path}, 是否继续?`, '提示', {
    confirmButtonText: '删除',
    cancelButtonText: '取消',
    type: 'warning',
  })
      .then(() => {
        ElMessage.success('删除成功');
        getTableData();
        //await setBackEndControlRefreshRoutes() // 刷新菜单，未进行后端接口测试
      })
      .catch(() => {
      });
};
// 页面加载时
onMounted(() => {
  getTableData();
});
</script>
<style>
.menu-map{
  margin-top: 20px;
}

.el-card__body{
  padding: 20px 10px;
}
</style>
