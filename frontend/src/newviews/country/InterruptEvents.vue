<template>
  <div class="system-menu-container layout-pd">
    <el-dialog v-model="state.judgeDialogVisible" title="研判事件" width="30%">
      <el-form ref="judgeruleFormRef" :model="judgeruleForm" :rules="judgerules" label-width="80px" class="demo-ruleForm" size="default" status-icon>
        <el-form-item label="事件信息" prop="event_info">
          <el-input v-model="judgeruleForm.event_info" type="textarea" :rows="3" disabled />
        </el-form-item>
        <el-form-item label="研判依据" prop="check_list" class="is-required" style="margin-bottom: 9px">
          <el-checkbox-group v-model="judgeruleForm.check_list">
            <el-checkbox label="前缀含有重要应用服务" />
            <el-checkbox label="前缀为重点关注前缀" />
            <el-checkbox label="AS为重要服务AS（云服务商，银行，证券）" />
            <el-checkbox label="AS为国家关键传输节点" />
          </el-checkbox-group>
        </el-form-item>
        <el-form-item prop="input_reason" style="margin-bottom: 27px">
          <el-input v-model="judgeruleForm.input_reason" type="textarea" :rows="3" />
        </el-form-item>
        <el-form-item label="研判结论" prop="judge_result">
          <!-- 修改：下拉选择 -> 单选 -->
          <el-radio-group v-model="judgeruleForm.judge_result">
            <el-radio label="suspected">疑似事件</el-radio>
            <el-radio label="notify">待通报事件</el-radio>
            <el-radio label="misreport">误报事件</el-radio>
          </el-radio-group>
        </el-form-item>
      </el-form>
      <template #footer>
				<span class="dialog-footer">
					<el-button @click="judgeresetForm(judgeruleFormRef)">取消</el-button>
					<el-button type="primary" @click="judgesubmitForm(judgeruleFormRef)">确定</el-button>
				</span>
      </template>
    </el-dialog>
    <el-card shadow="hover">
      <div class="system-menu-search mb15" style="display: flex">
        <el-select size="default" v-model="state.level" class="ml10" placeholder="事件等级" style="max-width: 150px">
          <el-option v-for="item in levels" :key="item.value" :label="item.label" :value="item.value" />
        </el-select>
        <!-- 更改：机构 -> 肇事方和受害方 -->
        <el-input size="default" v-model="state.attacker_org" placeholder="出口边界" style="max-width: 90px" class="ml10"> </el-input>
        <el-input size="default" v-model="state.attacked_org" placeholder="出口下一跳" style="max-width: 90px" class="ml10"> </el-input>
<!--        <el-input size="default" v-model="state.event_info" placeholder="前缀、AS名称、国家" style="max-width: 210px" class="ml10"> </el-input>-->
        <el-date-picker
            v-model="state.start_time"
            type="daterange"
            range-separator="-"
            start-placeholder="开始时间起点"
            end-placeholder="开始时间终点"
            class="ml10"
            size="default"
            style="max-width: 280px"
        />
        <el-button size="default" type="primary" class="ml15" style="width: 80px" @click="search"> 查询 </el-button>
        <el-button size="default" class="ml15" style="width: 80px" @click="resetData"> 重置 </el-button>
        <el-tooltip class="box-item" effect="light" :content="`当前选中${state.multipleSelection.length}条`" placement="top" size="large">
          <el-button
              size="default"
              type="primary"
              class="ml15"
              style="width: 80px"
              @click="exportExcel"
              :disabled="state.multipleSelection.length === 0"
          >
            批量导出
          </el-button>
        </el-tooltip>
      </div>
      <div>
        <el-table
            ref="multipleTableRef"
            :data="state.data"
            v-loading="state.loading"
            style="width: 100%"
            @sort-change="handleSortChange"
            @selection-change="handleSelectionChange"
            :row-key="getRowKeys"
            size="default"
            border
        >
          <el-table-column type="selection" :reserve-selection="true" width="38" />
          <el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="序号" width="60" />
          <el-table-column prop="event_type" label="事件类型" width="110" sortable="custom" />
          <el-table-column prop="level" label="事件等级" width="110" sortable="custom" />
          <el-table-column prop="attacker_as" label="出口边界as" width="110" sortable="custom" />
          <el-table-column prop="attacker_org" label="出口边界机构" min-width="120" sortable="custom" />
          <el-table-column prop="attacker_country" label="出口边界国家" min-width="120" sortable="custom" />
          <el-table-column prop="attacked_as" label="出口下一跳as" width="110" sortable="custom" />
          <el-table-column prop="attacked_org" label="出口下一跳机构" min-width="120" sortable="custom" />
          <el-table-column prop="attacked_country" label="出口下一跳国家" min-width="120" sortable="custom" />
          <el-table-column prop="start_time" width="110" label="开始时间" sortable="custom" />
          <el-table-column prop="end_time" width="110" label="结束时间" sortable="custom" />
          <el-table-column label="操作" width="100">
            <template #default="scope">
              <el-button type="primary" link @click="toDetail(scope.row.detail_url, scope.row.event_type)">详情</el-button>
              <el-button v-if="userInfo.roles[0] !== 'guest'" type="primary" link @click="handleJudge(scope.row)" class="ml10">研判</el-button>
            </template>
          </el-table-column>
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
</template>

<script setup lang="ts" name="anomaly">
import baseUrl from "/@/api";
import { onMounted, reactive, ref } from 'vue';
import request from '/@/utils/request';
import { useRouter } from 'vue-router';
import type { FormInstance, FormRules } from 'element-plus';
import { ElMessage, ElTable } from 'element-plus';
import { useUserInfo } from '/@/stores/userInfo';
import { storeToRefs } from 'pinia';
// 多选
const multipleTableRef = ref<InstanceType<typeof ElTable>>();
const getRowKeys = (row) => {
  return row.detail_url;
};
const handleSelectionChange = (val) => {
  state.multipleSelection = val;
};
// 导出Excel
const exportExcel = async () => {
  const rows = [];
  for (let item of state.multipleSelection) {
    rows.push(item.detail_url);
  }
  const download_url = await request({
    // url: 'http://10.3.242.226:19746/excel-export',
    url: baseUrl + 'reports/excel-export',
    method: 'post',
    data: {
      state: 'judge',
      rows: rows,
    },
  });
  // const url = 'http://10.3.242.226:19746/download/' + download_url;
  const url = baseUrl + 'reports/download/' + download_url;
  const a = document.createElement('a');
  a.style.display = 'none';
  a.href = url;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};
//当前用户信息
const stores = useUserInfo();
const { userInfo } = storeToRefs(stores);

const levels = [
  {
    value: 'high',
    label: '高危事件',
  },
  {
    value: 'middle',
    label: '中危事件',
  },
  {
    value: 'low',
    label: '低危事件',
  },
];
const state = reactive({
  data: [] as Array<any>,
  total_page: 1,
  total_cnt: 0,
  current_page: 1,
  page_size: 10,
  loading: true,
  event_type: '边界中断',
  level: '',
  // 更改：机构 -> 肇事方和受害方
  attacker_org: '',
  attacked_org: '',
  // org: '',
  event_info: '',
  start_time: '',
  sort_mode: '',
  judgeDialogVisible: false,
  multipleSelection: [],
});

const router = useRouter();
const toDetail = (detail_url, event_type) => {
  // 打开新页签
  const jumpurl = router.resolve({ name: 'anomaly_detail', query: { detail_url: detail_url, type: event_type } }); //带参跳转
  window.open(jumpurl.href);
  // 不打开新页签
  // router.push({
  // 	path: '/anomaly/detail',
  // 	query: { detail_url: detail_url, type: event_type },
  // });
};
// Date转String
const dateToString = (date: any) => {
  let year = date.getFullYear();
  let month = (date.getMonth() + 1).toString();
  let day = date.getDate().toString();
  if (month.length < 2) {
    month = '0' + month;
  }
  if (day.length < 2) {
    day = '0' + day;
  }
  return `${year}-${month}-${day}`;
};
// level汉化
const mapLevel = {
  high: '高危事件',
  middle: '中危事件',
  low: '低危事件',
};
// 获取列表数据，初始化表格
const getTableData = async () => {
  state.loading = true;
  // 获取全部数据
  try {
    const res = await request({
      // url: 'http://10.3.242.226:19746/event',
      url: baseUrl + 'events',
      method: 'get',
      params: {
        state: 'judge',
        page_num: state.current_page,
        page_size: state.page_size,
        event_type: state.event_type,
        level: state.level,
        // 更改：机构 -> 出口边界和出口下一跳
        attacker_org: state.attacker_org,
        attacked_org: state.attacked_org,
        // org: state.org,
        event_info: state.event_info,
        start_time: state.start_time === '' ? '' : dateToString(state.start_time[0]) + '_' + dateToString(state.start_time[1]),
        sort_mode: state.sort_mode,
      },
    });
    res.data.forEach((item) => {
      item.level = mapLevel[item.level];
    });
    state.total_page = res.total_page > 0 ? res.total_page : 1;
    state.total_cnt = res.record_count;
    state.data = res.data;
  } catch (e) {
    // ElMessage.warning('网络异常，请稍后再试');
  } finally {
    state.loading = false;
  }
};
// 重置
const resetData = () => {
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  state.page_size = 10;
  state.event_type = '边界中断';
  state.level = '';
  // 更改：机构 -> 出口边界和出口下一跳
  state.attacker_org = '',
  state.attacked_org = '',
  // state.org = '';
  state.event_info = '';
  state.start_time = '';
  state.sort_mode = '';
  state.multipleSelection = [];
  multipleTableRef.value!.clearSelection();
  multipleTableRef.value!.clearSort();
  getTableData();
};
// 查询
const search = () => {
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  state.sort_mode = '';
  state.multipleSelection = [];
  multipleTableRef.value!.clearSelection();
  multipleTableRef.value!.clearSort();
  getTableData();
};
const handleCurrentChange = (val: number) => {
  state.current_page = val;
  getTableData();
};
const handleSizeChange = (val: number) => {
  state.current_page = 1;
  state.page_size = val;
  getTableData();
};
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
// 研判
const judgeruleFormRef = ref<FormInstance>();
const judgeruleForm = reactive({
  detail_url: '',
  event_info: '',
  check_list: [],
  input_reason: '',
  judge_result: '',
});
const validateInputReason = (rule: any, value: any, callback: any) => {
  if (value.trim() === '' && judgeruleForm.check_list.length === 0) {
    callback(new Error('研判依据不能为空'));
  } else {
    callback();
  }
};
const judgerules = reactive<FormRules>({
  input_reason: [{ validator: validateInputReason, trigger: 'blur' }],
  judge_result: [{ required: true, message: '研判结论不能为空', trigger: 'change' }],
});
const judgesubmitForm = async (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  await formEl.validate((valid, fields) => {
    if (valid) {
      judge().then(() => {
        formEl.resetFields();
      });
      state.judgeDialogVisible = false;
    } else {
      console.log('error submit!', fields);
    }
  });
};
const judgeresetForm = (formEl: FormInstance | undefined) => {
  if (!formEl) return;
  formEl.resetFields();
  state.judgeDialogVisible = false;
};
const getJudgeReason = () => {
  let { check_list, input_reason } = judgeruleForm;
  input_reason = input_reason.trim();
  if (input_reason !== '') {
    check_list.push(input_reason);
  }
  return check_list.join('；');
};
const judge = async () => {
  const judgeResult = await request({
    // url: 'http://10.3.242.226:19746/judge',
    url: baseUrl + 'events/judge',
    method: 'post',
    data: {
      detail_url: judgeruleForm.detail_url,
      judge_reason: getJudgeReason(),
      state: judgeruleForm.judge_result,
    },
  });
  if (judgeResult.status) {
    ElMessage.success('事件研判成功');
    getTableData();
  } else {
    const msg = judgeResult.msg ? judgeResult.msg : '事件研判失败';
    ElMessage.error(msg);
  }
};
const handleJudge = (row) => {
  judgeruleForm.detail_url = row.detail_url;
  judgeruleForm.event_info = row.event_info;
  state.judgeDialogVisible = true;
};
// 页面加载时
onMounted(() => {
  getTableData();
});
</script>
<style lang="scss" scoped>
.el-card__body {
  padding: 20px 10px;
}
.cell {
  button {
    width: 28px;
    margin: 5px 0;
  }
}
</style>
