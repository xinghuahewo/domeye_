<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex;position: relative">
				<el-select size="default" v-model="state.level" class="ml10" placeholder="事件等级" style="max-width: 120px">
					<el-option v-for="item in levels" :key="item.value" :label="item.label" :value="item.value" />
				</el-select>
				
				<!-- 更改：机构 -> 肇事方和受害方 -->
				<el-input size="default" v-model="state.attacker_as" placeholder="肇事方AS" style="max-width: 120px" class="ml10"> </el-input>
				<el-input size="default" v-model="state.attacked_as" placeholder="受害方AS" style="max-width: 120px" class="ml10"> </el-input>
				

				<!-- 更改：机构 -> 肇事方和受害方 -->
				<el-input size="default" v-model="state.attacker_org" placeholder="肇事方机构" style="max-width: 120px" class="ml10"> </el-input>
				<el-input size="default" v-model="state.attacked_org" placeholder="受害方机构" style="max-width: 120px" class="ml10"> </el-input>
<!--				<el-input size="default" v-model="state.org" placeholder="机构名称" style="max-width: 150px" class="ml10"> </el-input>-->
				<!--<el-input size="default" v-model="state.event_info" placeholder="事件信息" style="max-width: 120px" class="ml10"> </el-input>-->
				<el-input size="default" v-model="state.attacker_country" placeholder="肇事方国家" style="max-width: 120px" class="ml10"> </el-input>
				<el-input size="default" v-model="state.attacked_country" placeholder="受害方国家" style="max-width: 120px" class="ml10"> </el-input>
				<el-date-picker
					v-model="state.datetime"
					type="datetimerange"
					range-separator="To"
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
          <el-table-column prop="affected_prefix" label="影响前缀" min-width="130" />
          <el-table-column prop="attacked_as" label="受害方AS" min-width="100" sortable="custom" />
          <el-table-column prop="attacked_org" label="受害方机构" min-width="100" sortable="custom" />
          <el-table-column prop="attacked_country" label="受害方国家" width="120" sortable="custom" />
          <el-table-column prop="attacker_as" label="肇事方AS" min-width="100" sortable="custom" />
          <el-table-column prop="attacker_org" label="肇事方机构" min-width="100" sortable="custom" />
          <el-table-column prop="attacker_country" label="肇事方国家" width="120" sortable="custom" />
          <el-table-column prop="level" label="事件等级" width="110" sortable="custom">
            <template #default="scope">
              <span :class="{highLevel: scope.row.level === '高危事件', middleLevel: scope.row.level === '中危事件', lowLevel: scope.row.level === '低危事件'}">{{ scope.row.level }}</span>
            </template>
          </el-table-column>
          <el-table-column prop="start_time" width="110" label="开始时间" sortable="custom" />
          <el-table-column prop="end_time" width="110" label="结束时间" sortable="custom" />
					<el-table-column label="操作" width="170">
						<template #default="scope">
							<div style="display: flex; align-items: center; gap: 10px;">
								<el-button type="primary" link @click="toDetail(scope.row.detail_url, scope.row.event_type)">详情</el-button>
								<TemplateSelectionDialog :detail-url="scope.row.detail_url" />
							</div>
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
import { onMounted, reactive, ref, watch } from 'vue';
import request from '/@/utils/request';
import { useRouter, useRoute } from 'vue-router';
import { ElTable } from 'element-plus';
import { ElMessage } from 'element-plus';
import baseUrl from "/@/api";
import TemplateSelectionDialog from '/@/components/home/TemplateSelectionDialog.vue';

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
	source: '', // default source if needed
	event_type: '',
	level: '',
	// as
	attacker_as: '',
	attacked_as: '',
  	// 更改：机构 -> 肇事方和受害方
	attacker_org: '',
  	attacked_org: '',
	attacked_country: '',
	attacker_country: '',
  	// org: '',
	event_info: '',
	datetime: '',
	sort_mode: '',
	multipleSelection: [],
});

const router = useRouter();
const route = useRoute();

const toDetail = (detail_url, event_type) => {
	const jumpurl = router.resolve({ name: 'anomaly_detail', query: { detail_url: detail_url, type: event_type } }); //带参跳转
	window.open(jumpurl.href);
};
// Date转String
// const dateToString = (date: any) => {
// 	let year = date.getFullYear();
// 	let month = (date.getMonth() + 1).toString();
// 	let day = date.getDate().toString();
// 	if (month.length < 2) {
// 		month = '0' + month;
// 	}
// 	if (day.length < 2) {
// 		day = '0' + day;
// 	}
// 	return `${year}-${month}-${day}`;
// };
const dateToString = (date: any) => {
	console.log(date);
	let year = date.getFullYear();
	let month = (date.getMonth() + 1).toString();
	let day = date.getDate().toString();
	// 新增时间部分
	let hours = date.getHours().toString();
	let minutes = date.getMinutes().toString();
	let seconds = date.getSeconds().toString();
	
	// 补零处理
	if (month.length < 2) month = '0' + month;
	if (day.length < 2) day = '0' + day;
	if (hours.length < 2) hours = '0' + hours;
	if (minutes.length < 2) minutes = '0' + minutes;
	if (seconds.length < 2) seconds = '0' + seconds;
	
	// 返回包含时间戳的格式
	return `${year}-${month}-${day} ${hours}:${minutes}:${seconds}`;
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
	// console.log("date:", state.date)
	// 获取全部数据
	try {
		const res = await request({
      // url: 'http://10.3.242.226:19746/events',
			// url: baseUrl + 'events',
      url: baseUrl + 'events',
			method: 'get',
			params: {
				country: 'foreign',
				page_num: state.current_page,
				page_size: state.page_size,
				source: state.source,
				event_type: state.event_type === '' ? 'all' : state.event_type,
				level: state.level === '' ? 'all' : state.level,
				// as -> 肇事方和受害方
				attacker_as: state.attacker_as,
				attacked_as: state.attacked_as,
        		// 更改：机构 -> 肇事方和受害方
        		attacker_org: state.attacker_org,
        		attacked_org: state.attacked_org,
				attacked_country: state.attacked_country,
				attacker_country: state.attacker_country,
				// org: state.org,
				event_info: state.event_info,
				datetime: state.datetime === '' ? '' : dateToString(state.datetime[0]) + '_' + dateToString(state.datetime[1]),
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
	state.source = '';
	// state.event_type = ''; // event_type is determined by route
	state.level = '';
  	state.attacked_as = '',
	state.attacker_as = '';
	// 更改：机构 -> 肇事方和受害方
  	state.attacker_org = '',
  	state.attacked_org = '',
	state.attacked_country = '';
	state.attacker_country = '';
	// state.org = '';
	state.event_info = '';
	state.date = '';
	state.sort_mode = '';
  	state.multipleSelection = [];
  	multipleTableRef.value!.clearSelection()
  	multipleTableRef.value!.clearSort()
	getTableData();
};
// 查询
const search = () => {
	state.total_page = 1;
	state.total_cnt = 0;
	state.current_page = 1;
  	state.sort_mode = '';
  	state.multipleSelection = [];
  	multipleTableRef.value!.clearSelection()
  	multipleTableRef.value!.clearSort()
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
			state: 'abroad',
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
// 导出模板
const template = async (detail_url) => {
  state.loading = true
	const download_url = await request({
    // url: 'http://10.3.242.226:19746/template-export',
		url: baseUrl + 'reports/template-export',
		method: 'post',
		data: {
			detail_url: detail_url,
		},
	});
  state.loading = false
  ElMessage({
    message: download_url.split("-")[5] + '模板已生成',
    type: 'success',
  })
  // const url = 'http://10.3.242.226:19746/download/' + download_url;
  const url = baseUrl + 'reports/download/' + download_url;
  console.log(url)
  const a = document.createElement('a');
  a.style.display = 'none';
  a.href = url;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
};

// 页面加载时
onMounted(() => {
	state.event_type = route.meta.title as string;
	getTableData();
});

watch(
	() => route.path,
	() => {
		if (route.path.startsWith('/abroad/')) {
			state.event_type = route.meta.title as string;
			resetData();
		}
	}
);
</script>
<style lang="scss" scoped>
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

:deep(.el-table td.el-table__cell div){
  white-space: pre-wrap;
}
</style>
