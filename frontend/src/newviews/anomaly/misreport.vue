<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex">
				<el-select size="default" v-model="state.event_type" placeholder="事件类型" style="max-width: 150px">
					<el-option v-for="item in event_types" :key="item.value" :label="item.label" :value="item.value" />
				</el-select>
				<el-select size="default" v-model="state.level" class="ml10" placeholder="事件等级" style="max-width: 150px">
					<el-option v-for="item in levels" :key="item.value" :label="item.label" :value="item.value" />
				</el-select>
				<el-input size="default" v-model="state.attacker_as" placeholder="肇事方AS" style="max-width: 90px" class="ml10"> </el-input>
        		<el-input size="default" v-model="state.attacked_as" placeholder="受害方AS" style="max-width: 90px" class="ml10"> </el-input>
        		<!-- 更改：机构 -> 肇事方和受害方 -->
        		<el-input size="default" v-model="state.attacker_org" placeholder="肇事方机构" style="max-width: 90px" class="ml10"> </el-input>
        		<el-input size="default" v-model="state.attacked_org" placeholder="受害方机构" style="max-width: 90px" class="ml10"> </el-input>
        <!--				<el-input size="default" v-model="state.org" placeholder="机构名称" style="max-width: 150px" class="ml10"> </el-input>-->
<!--        <el-input size="default" v-model="state.event_info" placeholder="前缀、AS名称、国家" style="max-width: 150px" class="ml10"> </el-input>-->
				<el-date-picker
					v-model="state.start_time"
					type="datetimerange"
					range-separator="-"
					start-placeholder="开始时间起点"
					end-placeholder="开始时间终点"
					class="ml10"
					size="default"
					style="max-width: 280px"
				/>
				<el-input size="default" v-model="state.judge_reason" placeholder="研判依据" style="max-width: 150px" class="ml10"> </el-input>
				<el-input size="default" v-model="state.judge_username" placeholder="研判人" style="max-width: 150px" class="ml10"> </el-input>
			</div>
			<div style="display: flex;position: relative">
				<el-date-picker
					v-model="state.judge_time"
					type="datetimerange"
					range-separator="-"
					start-placeholder="研判时间起点"
					end-placeholder="研判时间终点"
					size="default"
					style="max-width: 280px"
				/>
				<el-button size="default" type="primary" class="ml15" style="width: 80px" @click="search()"> 查询 </el-button>
				<el-button size="default" class="ml15" style="width: 80px" @click="resetData()"> 重置 </el-button>
				<el-tooltip class="box-item" effect="light" :content="`当前选中${state.multipleSelection.length}条`" placement="top" size="large">
					<el-button
						size="default"
						type="primary"
						class="ml15"
						style="width: 80px;position: absolute;right: 0"
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
					style="width: 100%; margin-top: 15px"
					@sort-change="handleSortChange"
					@selection-change="handleSelectionChange"
					:row-key="getRowKeys"
					size="default"
					border
				>
          <el-table-column type="selection" :reserve-selection="true" width="38" />
          <el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="序号" width="60" />
          <el-table-column prop="event_type" label="事件类型" width="110" sortable="custom" />
          <el-table-column prop="affected_prefix" label="影响前缀" min-width="120" />
          <el-table-column prop="attacked_as" label="受害方AS" min-width="80" sortable="custom" />
          <el-table-column prop="attacked_org" label="受害方机构" min-width="80" sortable="custom" />
          <el-table-column prop="attacked_country" label="受害方国家" width="120" sortable="custom" />
          <el-table-column prop="attacker_as" label="肇事方AS" min-width="80" sortable="custom" />
          <el-table-column prop="attacker_org" label="肇事方机构" min-width="80" sortable="custom" />
          <el-table-column prop="attacker_country" label="肇事方国家" width="120" sortable="custom" />
          <el-table-column prop="level" label="事件等级" width="110" sortable="custom">
            <template #default="scope">
              <span :class="{highLevel: scope.row.level === '高危事件', middleLevel: scope.row.level === '中危事件', lowLevel: scope.row.level === '低危事件'}">{{ scope.row.level }}</span>
            </template>
          </el-table-column>
					<el-table-column prop="start_time" width="110" label="开始时间" sortable="custom" />
					<el-table-column prop="end_time" width="110" label="结束时间" sortable="custom" />
					<el-table-column prop="judge_reason" width="110" label="研判依据" sortable="custom" />
					<el-table-column prop="judge_username" width="95" label="研判人" sortable="custom" />
					<el-table-column prop="judge_time" width="110" label="研判时间" sortable="custom" />
					<el-table-column label="操作" width="55">
						<template #default="scope">
							<el-button type="primary" link @click="toDetail(scope.row.detail_url, scope.row.event_type)">详情</el-button>
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
import { onMounted, reactive, ref } from 'vue';
import request from '/@/utils/request';
import { useRouter } from 'vue-router';
import { ElTable } from 'element-plus';
import baseUrl from "/@/api";
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
			state: 'misreport',
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
const event_types = [
	{
		value: '前缀劫持',
		label: '前缀劫持',
	},
	{
		value: '子前缀劫持',
		label: '子前缀劫持',
	},
	{
		value: '前缀中断',
		label: '前缀中断',
	},
	{
		value: 'AS中断',
		label: 'AS中断',
	},
	{
		value: '国家中断',
		label: '国家中断',
	},
	{
		value: '路由泄漏',
		label: '路由泄漏',
	},
  {
    value: 'RPKI证书异常',
    label: 'RPKI证书异常',
  },
];
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
	event_type: '',
	level: '',
	attacker_as: '',
	attacked_as: '',
  	// 更改：机构 -> 肇事方和受害方
  	attacker_org: '',
  	attacked_org: '',
  	// org: '',
	event_info: '',
	start_time: '',
	judge_reason: '',
	judge_username: '',
	judge_time: '',
	sort_mode: '',
	multipleSelection: [],
});

const router = useRouter();
const toDetail = (detail_url, event_type) => {
	const jumpurl = router.resolve({ name: 'anomaly_detail', query: { detail_url: detail_url, type: event_type } }); //带参跳转
	window.open(jumpurl.href);
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
				state: 'misreport',
				page_num: state.current_page,
				page_size: state.page_size,
				event_type: state.event_type,
				level: state.level,
				attacker_as: state.attacker_as,
				attacked_as: state.attacked_as,
        		// 更改：机构 -> 肇事方和受害方
        		attacker_org: state.attacker_org,
        		attacked_org: state.attacked_org,
        		// org: state.org,
				event_info: state.event_info,
				start_time: state.start_time === '' ? '' : dateToString(state.start_time[0]) + '_' + dateToString(state.start_time[1]),
				sort_mode: state.sort_mode,
        // 修改 添加筛选条件
        judge_reason: state.judge_reason,
        judge_username: state.judge_username,
        judge_time: state.judge_time === '' ? '' : dateToString(state.judge_time[0]) + '_' + dateToString(state.judge_time[1]),
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
	state.event_type = '';
	state.level = '';
	state.attacked_as = '';
	state.attacker_as = '';
  	// 更改：机构 -> 肇事方和受害方
  	state.attacker_org = '',
  	state.attacked_org = '',
  	// state.org = '';
	state.event_info = '';
	state.start_time = '';
	state.sort_mode = '';
	state.multipleSelection = [];

  	// 修改 添加重置
  	state.judge_reason = '',
  	state.judge_username = '',
  	state.judge_time = '',

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
// 页面加载时
onMounted(() => {
	getTableData();
});
</script>
<style lang="scss" scoped>

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
