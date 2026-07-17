<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex;position: relative">
				<el-input size="default" v-model="state.asn" placeholder="ASN" style="max-width: 150px"> </el-input>
				<el-button size="default" type="primary" class="ml15" style="width: 80px" @click="search"> 查询 </el-button>
				<el-button size="default" class="ml15" style="width: 80px" @click="resetData"> 重置 </el-button>
			</div>
			<div>
				<el-table
					:data="state.data"
					v-loading="state.loading"
					style="width: 100%"
					size="default"
					border
				>
					<el-table-column type="index" :index="(state.current_page - 1) * state.page_size + 1" label="ID" width="60" />
					<el-table-column prop="asn" label="ASN" min-width="100" />
					<el-table-column prop="as_name" label="AS名称" min-width="150" />
					<el-table-column prop="as_rank" label="AS rank" min-width="100" />
					<el-table-column prop="ipv4_prefixes" label="IPv4前缀数量" min-width="120" />
					<el-table-column prop="ipv6_prefixes" label="IPv6前缀数量" min-width="120" />
					<el-table-column prop="latest_time" label="最新计算时间" min-width="160" />
					<el-table-column prop="status" label="状态" min-width="100">
						<template #default="scope">
							<el-tag :type="scope.row.status === '正常' ? 'success' : 'danger'" disable-transitions>
								{{ scope.row.status || '正常' }}
							</el-tag>
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

<script setup lang="ts" name="nodeStatus">
import { onMounted, reactive } from 'vue';
import request from '/@/utils/request';

interface NodeStatusRow {
	asn: string;
	as_name: string;
	as_rank: number | string;
	ipv4_prefixes: number;
	ipv6_prefixes: number;
	latest_time: string;
	status: string;
}

const state = reactive({
	data: [] as NodeStatusRow[],
	total_page: 1,
	total_cnt: 0,
	current_page: 1,
	page_size: 10,
	loading: false,
	asn: '',
});

const getTableData = async () => {
	state.loading = true;
	try {
		const res = await request({
			url: 'node-status',
			method: 'get',
			params: {
				page_num: state.current_page,
				page_size: state.page_size,
				asn: state.asn.trim(),
			},
		});

		state.total_page = Number(res?.total_page) > 0 ? Number(res.total_page) : 1;
		state.total_cnt = Number(res?.record_count) || 0;
		state.data = Array.isArray(res?.data) ? res.data : [];
	} finally {
		state.loading = false;
	}
};

const search = () => {
	state.current_page = 1;
	getTableData();
};

const resetData = () => {
	state.asn = '';
	state.current_page = 1;
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

onMounted(() => {
	getTableData();
});
</script>

<style lang="scss" scoped>
.el-card__body {
	padding: 20px 10px;
}
</style>
