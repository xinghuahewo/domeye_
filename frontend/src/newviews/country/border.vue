<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex">
				<el-input size="default" v-model="state.export_as_country" placeholder="请输入起始国" style="max-width: 150px"> </el-input>
				<el-input size="default" v-model="state.peer_as_country" placeholder="请输入边界国" style="max-width: 150px; margin-left: 10px"> </el-input>
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
<!--				<div style="display: flex; margin-left: auto">-->
<!--					<el-button size="default" type="primary" class="ml15" style="width: 80px"> 视图 </el-button>-->
<!--				</div>-->
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
					id="table"
				>
					<el-table-column type="selection" :reserve-selection="true" width="38" />
					<el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="序号" width="60" />
					<el-table-column prop="export_as_country" label="起始国" width="80" sortable="custom" />
					<el-table-column prop="export_as" label="起始AS" width="100" sortable="custom" />
					<el-table-column prop="export_as_name" label="起始AS名称" width="130" sortable="custom" />
					<el-table-column prop="export_as_org" label="起始AS机构" sortable="custom" />
					<el-table-column prop="export_as_type" label="起始AS类型" width="130" sortable="custom" />
					<el-table-column prop="peer_as_country" label="边界国" width="80" sortable="custom" />
					<el-table-column prop="peer_as" label="边界AS" width="100" sortable="custom" />
					<el-table-column prop="peer_as_name" label="边界AS名称" width="130" sortable="custom" />
					<el-table-column prop="peer_as_org" label="边界AS机构" sortable="custom" />
					<el-table-column prop="peer_as_type" label="边界AS类型" width="130" sortable="custom" />
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
import { ElTable } from 'element-plus';
import baseUrl from "/@/api";

const state = reactive({
	data: [] as Array<any>,
	total_page: 1,
	total_cnt: 0,
	current_page: 1,
	page_size: 10,
	loading: true,
	export_as_country: '',
	peer_as_country: '',
	sort_mode: '',
	multipleSelection: [],
});

// 获取列表数据，初始化表格
const getTableData = async () => {
	state.loading = true;
	// 获取全部数据
	try {
		const res = await request({
      // url: 'http://10.3.242.226:19746/boundary',
      url: baseUrl + 'geodata/boundaries',
			method: 'get',
			params: {
				page_num: state.current_page,
				page_size: state.page_size,
				export_as_country: state.export_as_country,
				peer_as_country: state.peer_as_country,
				sort_mode: state.sort_mode,
			},
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
	state.export_as_country = '';
	state.peer_as_country = '';
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
	return `${row.export_as}-${row.peer_as}`;
};
const handleSelectionChange = (val) => {
	state.multipleSelection = val;
};

// 导出Excel
const exportExcel = async () => {
	const rows = [];
	for (let item of state.multipleSelection) {
		rows.push(`${item.export_as}-${item.peer_as}`);
	}
	const download_url = await request({
    // url: 'http://10.3.242.226:19746/excel-export-country',
		url: baseUrl + 'reports/excel-export-country',
		method: 'post',
		data: {
			type: 'border',
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
<style lang="scss">
#table .caret-wrapper {
	width: 12px;
}
</style>
