<template>
  <div>
    <el-menu
        class="el-menu-vertical-demo"
        @select="sendmsg"
    >
      <el-menu-item v-for="i in props.menuList" :index="i.index">
        <el-icon>
          <document/>
        </el-icon>
        <span>{{ i.name }}</span>
      </el-menu-item>
    </el-menu>
  </div>

</template>

<script lang="ts" setup>
import {
  Document,
} from '@element-plus/icons-vue'
import {reactive} from "vue";
import {RouteRecordRaw} from "vue-router";

const state = reactive({
  tableData: {
    data: [] as RouteRecordRaw[],
    loading: true,
  },
});
// 定义emit，向父组件发送数据
const emit = defineEmits(["getValue"])
const sendmsg = (key) => {
  emit('getValue', key)
}
// 定义props，从父组件接收数据
const props = defineProps({
  // 菜单列表
  menuList: {
    type: Array < RouteRecordRaw >,
    default: () => [],
  },
});


</script>
<style scoped lang="scss">
.el-menu {
  border-right: solid 1px var(--el-menu-border-color) !important;
  list-style: none;
  position: relative;
  margin: 0;
  padding-left: 0;
  background-color: #fff;
  box-sizing: border-box;
}
.el-menu-item {
  color: var(--el-menu-text-color);
}
.el-menu-item.is-active {
  color: #409eff;
  background-color: #ffffff !important;
}
.el-menu-hover-bg-color {
  background-color: #ecf5ff !important;
}
.el-menu-item:hover {
  @extend .el-menu-hover-bg-color;
}
//.el-menu-item a:hover{
//  color: #3eaadc;
//}
</style>

