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
        <!-- 更改：机构 -> 肇事方和受害方 -->
        <el-input size="default" v-model="state.org1" placeholder="肇事方" style="max-width: 120px" class="ml10"> </el-input>
        <el-input size="default" v-model="state.org2" placeholder="受害方" style="max-width: 120px" class="ml10"> </el-input>
        <!--				<el-input size="default" v-model="state.org" placeholder="机构名称" style="max-width: 150px" class="ml10"> </el-input>-->
        <el-input size="default" v-model="state.event_info" placeholder="事件信息" style="max-width: 150px" class="ml10"> </el-input>
				<el-date-picker
					v-model="state.date"
					type="daterange"
					range-separator="To"
					start-placeholder="开始日期"
					end-placeholder="结束日期"
					class="ml10"
					size="default"
					style="max-width: 280px"
				/>
				<el-input size="default" v-model="state.operator" placeholder="研判人" style="max-width: 150px" class="ml10"> </el-input>
				<el-button size="default" type="primary" class="ml15" style="width: 80px" @click="search()"> 查询 </el-button>
				<el-button size="default" class="ml15" style="width: 80px" @click="resetData()"> 重置 </el-button>
			</div>
			<div>
				<el-table :data="state.data" v-loading="state.loading" style="width: 100%" row-key="index">
					<el-table-column type="index" :index="state.current_page * 20 - 19" label="序号" width="60" />
					<el-table-column prop="event_type" label="事件类型" width="100" />
					<el-table-column prop="level" label="事件等级" width="100" />
          <el-table-column prop="org1" label="肇事方" width="100" />
          <el-table-column prop="org2" label="受害方" width="100" />
<!--					<el-table-column prop="org" label="涉事机构" width="150" />-->
					<el-table-column prop="event_info" label="事件信息" />
					<el-table-column prop="start_time" width="100" label="开始时间" />
					<el-table-column prop="end_time" width="100" label="结束时间" />
					<el-table-column prop="operator" width="100" label="研判人" />
					<el-table-column label="详情" width="60">
						<template #default="scope">
							<el-button type="primary" link @click="toDetail(scope.row.detail_url, scope.row.event_type)">查看</el-button>
						</template>
					</el-table-column>
					<el-table-column label="处置结果" width="80">
						<el-button type="primary" link>查看</el-button>
					</el-table-column>
				</el-table>
				<div class="table-footer mt20">
					<el-pagination
						v-model:current-page="state.current_page"
						:page-size="20"
						:pager-count="15"
						:total="state.total_cnt"
						:page-count="state.total_page"
						background
						@current-change="onHandleCurrentChange"
					>
					</el-pagination>
				</div>
			</div>
		</el-card>
	</div>
</template>

<script setup lang="ts" name="anomaly">
import { onMounted, reactive } from 'vue';
import request from '/@/utils/request';
import baseUrl from "/@/api";
// import { ElMessage } from 'element-plus';
import { useRouter } from 'vue-router';
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
	loading: true,
	event_type: '',
	level: '',
  // 更改：机构 -> 肇事方和受害方
  org1: '',
  org2: '',
  // org: '',
	event_info: '',
	date: '',
	operator: '',
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
      // url: 'http://10.3.242.226:19746/handle',
			url: baseUrl + 'handle',
			method: 'get',
			params: {
        state: 'finished',
				page_num: state.current_page,
				event_type: state.event_type,
				level: state.level,
        // 更改：机构 -> 肇事方和受害方
        org1: state.org1,
        org2: state.org2,
        // org: state.org,
				event_info: state.event_info,
				date: state.date === '' ? '' : dateToString(state.date[0]) + '_' + dateToString(state.date[1]),
				operator: state.operator,
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
	state.event_type = '';
	state.level = '';
  // 更改：机构 -> 肇事方和受害方
  state.org1 = '',
  state.org2 = '',
  // state.org = '';
	state.event_info = '';
	state.date = '';
	// state.sort_mode = '';
	getTableData();
};
// 查询
const search = () => {
  state.total_page = 1;
  state.total_cnt = 0;
  state.current_page = 1;
  getTableData();
}
const onHandleCurrentChange = (val: number) => {
	state.current_page = val;
	getTableData();
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
</style>
