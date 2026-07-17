<template>
	<div class="system-menu-container layout-pd">
		<el-card shadow="hover">
			<div class="system-menu-search mb15" style="display: flex">
				<el-input size="default" v-model="state.input1" placeholder="请输入起始国" style="max-width: 180px"> </el-input>
				<el-input size="default" v-model="state.input2" placeholder="请输入目的国" style="max-width: 180px; margin-left: 10px"> </el-input>
				<el-button size="default" type="primary" class="ml15"> 拓扑视图 </el-button>
			</div>
			<div>
				<div style="width: 190px; float: left">
					<map-menu class="menu-map" @getValue="selectMenu" :menuList="state.menulist"></map-menu>
				</div>
				<div style="margin-left: 190px">
					<el-table :data="state.tableData.data" v-loading="state.tableData.loading" style="width: 100%" row-key="index">
						<el-table-column type="index" label="序号" width="60"/>
						<el-table-column prop="vp_country_chinese_name" label="起始国" />
						<el-table-column prop="dst_country_chinese_name" label="目的国" />
						<el-table-column prop="export_as_count" label="出口AS数量" />
						<el-table-column prop="key_path_count" label="关键路径数量" />
						<el-table-column prop="entrance_as_count" label="入口AS数量" />
						<el-table-column label="详情">
							<template #default="scope">
								<el-button type="primary" link @click="toDetail(scope.row)">查看</el-button>
							</template>
						</el-table-column>
					</el-table>
				</div>
			</div>
		</el-card>
	</div>
</template>

<script setup lang="ts" name="countryBorder">
import { defineAsyncComponent, onMounted, reactive } from 'vue';
import request from '/@/utils/request';
import baseUrl from "/@/api";

// 引入组件
const MapMenu = defineAsyncComponent(() => import('/@/components/menu_child/mapMenu.vue'));

const state = reactive({
	allDataList: [] as Array<any>,
	tableData: {
		data: [] as Array<any>,
		loading: true,
	},
	input1: '',
	input2: '',
	menulist: [] as Array<any>,
});
// 点击目录的回调
const selectMenu = (index: number) => {
	state.tableData.loading = true;
	let name: string = state.menulist[index].name;
	let pos = name.lastIndexOf('(');
	let country = name.slice(0, pos);
	if (index == 0) {
		state.tableData.data = state.allDataList;
	} else {
		state.tableData.data = state.allDataList.filter((item) => {
			return item.vp_country_chinese_name == country || (item.vp_country_chinese_name === null && country === 'null');
		});
	}
	state.tableData.loading = false;
};

// 获取列表数据，初始化表格和机构目录
const initTableData = async () => {
	state.tableData.loading = true;
	// 获取全部数据
	const allDataList = await request({
    // url: 'http://10.3.242.226:19746/connection',
		url: baseUrl + 'geodata/connections',
		method: 'get',
	});
	state.allDataList = allDataList;
	// 初始化国家目录
	initMenuList();
	state.tableData.data = state.allDataList;
	state.tableData.loading = false;
};

// 根据列表生成目录数据
const initMenuList = () => {
	const countryDict = {};
	for (let item of state.allDataList) {
		let country = item.vp_country_chinese_name;
		if (!countryDict[country]) {
			countryDict[country] = 1;
		} else {
			countryDict[country] += 1;
		}
	}
	const countryList = [];
	for (let key in countryDict) {
		countryList.push([key, countryDict[key]]);
	}
	countryList.sort((a, b) => {
		return b[1] - a[1];
	});
	const menuList = [
		{
			index: 0,
			name: `全部(${state.allDataList.length})`,
		},
	];
	for (let i in countryList) {
		menuList.push({
			index: Number(i) + 1,
			name: `${countryList[i][0]}(${countryList[i][1]})`,
		});
	}
	state.menulist = menuList;
};
// 查看详情
const toDetail = (row) => {
	console.log(row);
};
// 页面加载时
onMounted(() => {
	initTableData();
});
</script>

<style scoped>
.el-table {
	border-left: solid 1px var(--el-menu-border-color) !important;
}
.el-card__body {
	padding: 20px 10px;
}
</style>
