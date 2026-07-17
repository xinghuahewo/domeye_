<template>
  <div class="system-menu-container layout-pd">
    <el-card shadow="hover">
      <div class="system-menu-search mb15" style="display: flex;position: relative">
        <el-input size="default" v-model="state.input" placeholder="查询内容" style="width: 240px"> </el-input>
        <el-button size="default" type="primary" class="ml15" style="width: 80px" @click="Search"> 查询 </el-button>
        <el-button size="default" class="ml15" style="width: 80px" @click="Reset"> 重置 </el-button>
        <el-button
            size="default"
            type="primary"
            class="ml15"
            style="width: 80px;position: absolute;right: 0"
            @click="uploadDialogVisible = true"
        >
          上传列表
        </el-button>
      </div>
      <div>
        <el-table
            ref="multipleTableRef"
            :data="state.data"
            v-loading="state.loading"
            style="width: 100%;"
            @sort-change="handleSortChange"
            :row-key="getRowKeys"
            size="default"
            border
        >
          <el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="序号" width="60" />
          <el-table-column prop="end_time" width="110" label="重点前缀" sortable="custom" />
        </el-table>
        <div class="table-footer mt20">
          <el-pagination
              v-model:current-page="state.current_page"
              v-model:page-size="state.page_size"
              :pager-count="15"
              :total="state.total_cnt"
              :page-count="state.total_page"
              :page-sizes="[10, 50, 100, 200]"
              background
              layout="prev, pager, next, jumper, sizes, ->, total"
              @current-change="handleCurrentChange"
              @size-change="handleSizeChange"
          >
          </el-pagination>
        </div>
      </div>
    </el-card>
  </div>


  <el-dialog v-model="uploadDialogVisible" title="上传列表" width="30%" center>
    <el-upload
        v-model:file-list="fileList"
        class="upload-demo"
        drag
        action="https://run.mocky.io/v3/9d059bf9-4660-45f2-925d-ce80ad6c4d15"
        multiple
    >
      <el-icon class="el-icon--upload"><upload-filled /></el-icon>
      <div class="el-upload__text">
        拖拽文件到此或 <em>点击进行上传</em>
      </div>
    </el-upload>
  </el-dialog>
</template>

<script setup lang="ts">
import {onMounted, reactive, ref} from "vue";
import { UploadFilled } from '@element-plus/icons-vue'
import type { UploadUserFile } from 'element-plus'

const state = reactive({
  data: [] as Array<any>,
  total_page: 1,
  total_cnt: 0,
  current_page: 1,
  page_size: 10,
  loading: false,
  sort_mode: '',
  input: '',
});

const getRowKeys = (row) => {
  return row.detail_url;
};

const getTableData = () => {

};

const Search = () => {
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  state.sort_mode = '';
  getTableData();
}

const Reset = () => {
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  state.page_size = 10;
  state.input = '';
  state.sort_mode = '';
  getTableData();
}

// 排序
const handleSortChange = (e) => {
  let { prop, order } = e;
  if (order === 'ascending') {
    state.sort_mode = prop + 'A';
  } else if (order === 'descending') {
    state.sort_mode = prop + 'B';
  }
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  getTableData();
};

const uploadDialogVisible = ref(false)

const fileList = ref<UploadUserFile[]>([
  {
    name: 'food.jpeg',
    url: 'https://fuss10.elemecdn.com/3/63/4e7f3a15429bfda99bce42a18cdd1jpeg.jpeg?imageMogr2/thumbnail/360x360/format/webp/quality/100',
  },
  {
    name: 'food2.jpeg',
    url: 'https://fuss10.elemecdn.com/3/63/4e7f3a15429bfda99bce42a18cdd1jpeg.jpeg?imageMogr2/thumbnail/360x360/format/webp/quality/100',
  },
])

// 页面加载时
onMounted(() => {
  getTableData();
});

</script>

<style scoped lang="scss">

:deep(.el-table td.el-table__cell div){
  white-space: pre-wrap;
}

.el-card__body {
  padding: 20px 10px;

.highLevel {
  color: red;
}
.middleLevel {
  color: orange;
}
.lowLevel {
  color: #dada01;
}
}
.cell {
button {
  width: 28px;
  margin: 5px 0;
}
}
</style>