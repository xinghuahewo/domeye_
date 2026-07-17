<template>
	<div class="home-container">
    <!-- 可以理解为<div>=<el-row> 、<span>等于<el-col> -->
		<!-- gutter是该row内元素之间的间隙 -->
		<el-row :gutter="15" class="home-card-one mb15">
			<el-col
				:xs="24"
				:sm="12"
				:md="12"
				:lg="6"
				:xl="6"
				v-for="(v, k) in homeOne"
				:key="k"
				:class="{ 'home-media home-media-lg': k > 1, 'home-media-sm': k === 1 }"
			>
				<div class="home-card-item flex">
					<div class="flex-margin flex w100" :class="` home-one-animation${k}`">
						<div class="flex-auto">
							<span class="font30">{{ v.num1 }}</span>
							<span class="ml5 font16" :style="{ color: v.color1 }">{{ v.num2 }}%</span>
							<div class="mt10">{{ v.num3 }}</div>
						</div>
						<div class="home-card-item-icon flex" :style="{ background: `var(${v.color2})` }">
							<i class="flex-margin font32" :class="v.num4" :style="{ color: `var(${v.color3})` }"></i>
						</div>
					</div>
				</div>
			</el-col>
		</el-row>
		<!-- 主页第2排 -->
		<el-row :gutter="15" class="home-card-two mb15">
			<el-col :xs="24" :sm="14" :md="14" :lg="16" :xl="16">
				<div class="home-card-item" style="padding-top:10px">
					<div class="home-card-item-title">事件地理分布</div>
					<div style="height: 95%" ref="echartsMap"></div>
				</div>
			</el-col>
			<el-col :xs="24" :sm="10" :md="10" :lg="8" :xl="8" class="home-media">
				<div class="home-card-item" style="padding-top:10px">
					<div class="home-card-item-title">重大路由事件</div>
					<el-table
						:data="tableData.slice(0, 3)"
						stripe
						style="width:100%;"
						v-infinite-scroll="load"
						@sort-change="sortChange">

						<el-table-column
							prop="event_type"
							label="事件类型"
							min-width="7%">
							</el-table-column>

						<el-table-column
							prop="event_type"
							label="AS"
							min-width="7%">
							</el-table-column>

						<el-table-column
							prop="event_type"
							label="国家"
							min-width="7%">
							</el-table-column>


						<el-table-column
							prop="start_time"
							label="发生时间"
							min-width="9%">
							<template  #default="scope">
								<span>{{scope.row.start_time.substring(0, scope.row.start_time.indexOf(' '))}}<br>{{scope.row.start_time.substring(scope.row.start_time.indexOf(' ')+1)}}</span>
							</template>
						</el-table-column>
					</el-table>
				</div>
			</el-col>
		</el-row>
		<!-- 主页第3排 -->
		<el-row :gutter="15" class="home-card-three">
			<el-col :xs="24" :sm="10" :md="10" :lg="8" :xl="8">
				<div class="home-card-item">
					<div class="home-card-item-title">中国移动路由情况</div>
					<div class="flex-title">
						<span>当前设备监测</span>
						<span class="flex-title-small">单位：次</span>
					</div>
					<div style="height: 90%" ref="chartsMonitorRef"></div>
				</div>
			</el-col>
			<el-col :xs="24" :sm="14" :md="14" :lg="8" :xl="16" class="home-media">
				<div class="home-card-item">
					<div class="home-card-item-title">中国电信路由情况</div>
					<div style="height: 90%" ref="chartsMonitorRef1"></div>
				</div>
			</el-col>
			<el-col :xs="24" :sm="10" :md="10" :lg="8" :xl="8">
				<div class="home-card-item">
					<div class="home-card-item-title">中国联通路由情况</div>
					<div style="height: 90%" ref="chartsMonitorRef2"></div>
				</div>
			</el-col>
		</el-row>
	</div>
</template>

<script lang="ts">
import { toRefs, reactive, computed, getCurrentInstance, defineComponent, onMounted, ref, watch, nextTick, onActivated } from 'vue';
import * as echarts from 'echarts';
import axios from 'axios';
import 'echarts/extension/bmap/bmap';
import { storeToRefs } from 'pinia';
import { useThemeConfig } from '/@/stores/themeConfig';
import { useTagsViewRoutes } from '/@/stores/tagsViewRoutes';
import { echartsMapList, echartsMapData } from '/@/views/fun/echartsMap/mock';

let global: any = {
	homeChartOne: null,
	homeChartTwo: null,
	homeCharThree: null,
	dispose: [null, '', undefined],
};

export default defineComponent({
	name: 'home',
	data() {
		return {
		server: 'http://your-backend-host/hijack',  // 对应flask服务的地址
		description: '',
		minWidth: '', // 用于存放单选框的长度
		TypeOptions: ['全部', '前缀劫持', '子前缀劫持', '路由泄露', '前缀中断', 'AS中断', '国家中断'],
		LevelOptions: ['全部','低危','中危','高危'],
		type: '全部',
		level: '全部',
		time_filter: '',
		search: '',
		searchValue: '',
		entryoptions: [{
			value: 5,
		}, {
			value: 10,
		}, {
			value: 20,
		}, {
			value: 25,
		}],
		entries: 10,
		originalData_type: [],
		originalData: [],  // 按怀疑级别排序后的原始数据
		tableData: [],
		currentPage:1,
		}
	},

	mounted() {
		// 向后台请求数据
		axios({
		methods: 'GET',
		url: this.server
		}).then( res => {
		// console.log(res)
		let originalData_ = res.data
		this.originalData = originalData_.filter(item => item.level == 'high')
		this.originalData = [...this.originalData, ...originalData_.filter(item => item.level == 'middle')]
		this.originalData = [...this.originalData, ...originalData_.filter(item => item.level == 'low')]
		this.tableData = this.originalData
		})
	},
	setup() {
		const homeLineRef = ref();
		const homePieRef = ref();
		const homeBarRef = ref();
		const { proxy } = <any>getCurrentInstance();
		const storesTagsViewRoutes = useTagsViewRoutes();
		const storesThemeConfig = useThemeConfig();
		const { themeConfig } = storeToRefs(storesThemeConfig);
		const { isTagsViewCurrenFull } = storeToRefs(storesTagsViewRoutes);
		const state = reactive({
			homeOne: [
				{
					num1: '125,12',
					num2: '-12.32',
					num3: '今日路由劫持事件',
					num4: 'fa fa-meetup',
					color1: '#FF6462',
					color2: '--next-color-primary-lighter',
					color3: '--el-color-primary',
				},
				{
					num1: '653,33',
					num2: '+42.32',
					num3: '今日路由泄露事件',
					num4: 'iconfont icon-ditu',
					color1: '#6690F9',
					color2: '--next-color-success-lighter',
					color3: '--el-color-success',
				},
				{
					num1: '125,65',
					num2: '+17.32',
					num3: '今日AS中断事件',
					num4: 'iconfont icon-zaosheng',
					color1: '#6690F9',
					color2: '--next-color-warning-lighter',
					color3: '--el-color-warning',
				},
				{
					num1: '520,43',
					num2: '-10.01',
					num3: '今日国家中断事件',
					num4: 'fa fa-github-alt',
					color1: '#FF6462',
					color2: '--next-color-danger-lighter',
					color3: '--el-color-danger',
				},
			],
			myCharts: [],
			charts: {
				theme: '',
				bgColor: '',
				color: '#303133',
			},
		});
		// 百度地图
		const baidu_state: any = reactive({
			echartsMap: null,
			echartsMapList,
			echartsMapData,
		});
		// 设置主内容的高度
		const initTagViewHeight = computed(() => {
			let { isTagsview } = themeConfig.value;
			if (isTagsViewCurrenFull.value) {
				return `30px`;
			} else {
				if (isTagsview) return `114px`;
				else return `80px`;
			}
		});
		// echartsMap 将坐标信息和对应物理量的值合在一起
		const convertData = (data: any) => {
			let res = [];
			for (let i = 0; i < data.length; i++) {
				let geoCoord = baidu_state.echartsMapData[data[i].name];
				if (geoCoord) {
					res.push({
						name: data[i].name,
						value: geoCoord.concat(data[i].value),
					});
				}
			}
			return res;
		};
		// 初始化 echartsMap
		const initEchartsMap = () => {
			const myChart = echarts.init(<HTMLElement>baidu_state.echartsMap);
			const option = {
				tooltip: {
					trigger: 'item',
				},
				color: ['#9a60b4', '#ea7ccc'],
				bmap: {
					center: [104.114129, 37.550339],
					zoom: 5,
					roam: true,
					mapStyle: {},
				},
				series: [
					{
						name: 'pm2.5',
						type: 'scatter',
						coordinateSystem: 'bmap',
						data: convertData(baidu_state.echartsMapList),
						symbolSize: function (val: any) {
							return val[2] / 10;
						},
						encode: {
							value: 2,
						},
						label: {
							formatter: '{b}',
							position: 'right',
							show: false,
						},
						emphasis: {
							label: {
								show: true,
							},
						},
					},
					{
						name: 'Top 5',
						type: 'effectScatter',
						coordinateSystem: 'bmap',
						data: convertData(
							baidu_state.echartsMapList
								.sort(function (a: any, b: any) {
									return b.value - a.value;
								})
								.slice(0, 6)
						),
						symbolSize: function (val: any) {
							return val[2] / 10;
						},
						encode: {
							value: 2,
						},
						showEffectOn: 'render',
						rippleEffect: {
							brushType: 'stroke',
						},
						hoverAnimation: true,
            // emphasis: {
            //   scale: true
            // },
            label: {
							formatter: '{b}',
							position: 'right',
							show: true,
						},
						itemStyle: {
							shadowBlur: 10,
							shadowColor: '#333',
						},
						zlevel: 1,
					},
				],
			};
			myChart.setOption(option);
			window.addEventListener('resize', () => {
				myChart.resize();
			});
		};

		// 移动联通电信折线图
		const initChartsMonitor = () => {
			const myChart = echarts.init(proxy.$refs.chartsMonitorRef);
			const myChart1 = echarts.init(proxy.$refs.chartsMonitorRef1);
			const myChart2 = echarts.init(proxy.$refs.chartsMonitorRef2);
			const option = {
				// grid:图标离容器的距离
				grid: {
					top: 15,
					right: 15,
					bottom: 20,
					left: 30,
				},
				// tooltip:提示框， trigger:'axis':坐标轴触发
				tooltip: {
					trigger: 'axis',
				},
				xAxis: {
					type: 'category',
					boundaryGap: false,
					data: ['02:00', '04:00', '06:00', '08:00', '10:00', '12:00', '14:00'],
				},
				yAxis: {
					type: 'value',
				},
				series: [
					{
						itemStyle: {
							color: '#289df5',
							borderColor: '#289df5',
							areaStyle: {
								type: 'default',
								opacity: 0.1,
							},
						},
						data: [20, 32, 31, 34, 12, 13, 20],
						type: 'line',
						areaStyle: {},
					},
				],
			};
			myChart.setOption(option);
			(<any>state.myCharts).push(myChart);
			myChart1.setOption(option);
			(<any>state.myCharts).push(myChart1);
			myChart2.setOption(option);
			(<any>state.myCharts).push(myChart2);
		};
		// 批量设置 echarts resize
		const initEchartsResizeFun = () => {
			nextTick(() => {
				for (let i = 0; i < state.myCharts.length; i++) {
					setTimeout(() => {
						(<any>state.myCharts[i]).resize();
					}, i * 1000);
				}
			});
		};
		// 批量设置 echarts resize
		const initEchartsResize = () => {
			window.addEventListener('resize', initEchartsResizeFun);
		};
		// 页面加载时
		onMounted(() => {
			initEchartsMap();
			initEchartsResize();
			initChartsMonitor();
		});
		// 由于页面缓存原因，keep-alive
		onActivated(() => {
			initEchartsResizeFun();
		});
		// 监听 vuex 中的 tagsview 开启全屏变化，重新 resize 图表，防止不出现/大小不变等
		watch(
			() => isTagsViewCurrenFull.value,
			() => {
				initEchartsResizeFun();
			}
		);
		// 监听 vuex 中是否开启深色主题
		watch(
			() => themeConfig.value.isIsDark,
			(isIsDark) => {
				nextTick(() => {
					state.charts.theme = isIsDark ? 'dark' : '';
					state.charts.bgColor = isIsDark ? 'transparent' : '';
					state.charts.color = isIsDark ? '#dadada' : '#303133';
					setTimeout(() => {
						// initLineChart();
					}, 500);
					setTimeout(() => {
						// initPieChart();
					}, 700);
					setTimeout(() => {
						// initBarChart();
					}, 1000);
				});
			},
			{
				deep: true,
				immediate: true,
			}
		);
		return {
			homeLineRef,
			homePieRef,
			homeBarRef,
			...toRefs(state),
			initTagViewHeight,
			...toRefs(baidu_state),
		};
	},
});
</script>

<style scoped lang="scss">
$homeNavLengh: 8;
.home-container {
	overflow: hidden;
	.home-card-one,
	.home-card-two,
	.home-card-three {
		.home-card-item {
			width: 100%;
			height: 130px;
			border-radius: 4px;
			transition: all ease 0.3s;
			padding: 20px;
			overflow: hidden;
			background: var(--el-color-white);
			color: var(--el-text-color-primary);
			border: 1px solid var(--next-border-color-light);
			&:hover {
				box-shadow: 0 2px 12px var(--next-color-dark-hover);
				transition: all ease 0.3s;
			}
			&-icon {
				width: 70px;
				height: 70px;
				border-radius: 100%;
				flex-shrink: 1;
				i {
					color: var(--el-text-color-placeholder);
				}
			}
			&-title {
				font-size: 15px;
				font-weight: bold;
				height: 30px;
			}
		}
	}
	.home-card-one {
		@for $i from 0 through 3 {
			.home-one-animation#{$i} {
				opacity: 0;
				animation-name: error-num;
				animation-duration: 0.5s;
				animation-fill-mode: forwards;
				animation-delay: calc($i/10) + s;
			}
		}
	}
	.home-card-two {
		.home-card-item {
			height: 550px;
			width: 100%;
			overflow: hidden;
			.home-monitor {
				height: 100%;
				.flex-warp-item {
					width: 25%;
					height: 111px;
					display: flex;
					.flex-warp-item-box {
						margin: auto;
						text-align: center;
						color: var(--el-text-color-primary);
						display: flex;
						border-radius: 5px;
						background: var(--next-bg-color);
						cursor: pointer;
						transition: all 0.3s ease;
						&:hover {
							background: var(--el-color-primary-light-9);
							transition: all 0.3s ease;
						}
					}
					@for $i from 0 through $homeNavLengh {
						.home-animation#{$i} {
							opacity: 0;
							animation-name: error-num;
							animation-duration: 0.5s;
							animation-fill-mode: forwards;
							animation-delay: calc($i/10) + s;
						}
					}
				}
			}
		}
	}
	.home-card-three {
		.home-card-item {
			height: 300px;
			width: 100%;
			overflow: hidden;
			.home-monitor {
				height: 100%;
				.flex-warp-item {
					width: 25%;
					height: 111px;
					display: flex;
					.flex-warp-item-box {
						margin: auto;
						text-align: center;
						color: var(--el-text-color-primary);
						display: flex;
						border-radius: 5px;
						background: var(--next-bg-color);
						cursor: pointer;
						transition: all 0.3s ease;
						&:hover {
							background: var(--el-color-primary-light-9);
							transition: all 0.3s ease;
						}
					}
					@for $i from 0 through $homeNavLengh {
						.home-animation#{$i} {
							opacity: 0;
							animation-name: error-num;
							animation-duration: 0.5s;
							animation-fill-mode: forwards;
							animation-delay: calc($i/10) + s;
						}
					}
				}
			}
		}
	}
}
</style>
