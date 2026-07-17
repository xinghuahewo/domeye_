<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<el-table :data="state.dataList" v-loading="state.loading" style="width: 100%" row-key="index">
				<el-table-column type="index" label="序号" width="60" />
				<el-table-column prop="event_type" label="事件类型" width="140" />
				<el-table-column prop="event_info" label="事件信息" />
        <el-table-column label="处置模板" width="100">
							<el-button type="primary" link>查看</el-button>
						</el-table-column>
        <el-table-column label="处置状态" width="100">
							<el-button type="primary" link>查看</el-button>
						</el-table-column>
        <el-table-column label="处置反馈" width="100">
							<el-button type="primary" link>查看</el-button>
						</el-table-column>
        <el-table-column label="应急处置" width="100">
							<el-button type="primary" link>查看</el-button>
						</el-table-column>
			</el-table>
		</el-card>
	</div>
</template>

<script setup lang="ts" name="handleUnfinished">
import { onMounted, reactive } from 'vue';
import request from '/@/utils/request';
import baseUrl from "/@/api";

const state = reactive({
	dataList: [] as Array<any>,
	loading: true,
});

const getDataList = async () => {
	state.loading = true;
	const dataList = await request({
		method: 'get',
    // url: 'http://10.3.242.226:19746/unfinished_handle',
		url: baseUrl + 'unfinished_handle',
	});
	state.dataList = dataList;
	state.loading = false;
};

onMounted(() => {
	getDataList();
});
</script>

<style scoped lang="scss">
.el-card__body {
	padding: 20px 10px;
}
</style>
