<template>
  <div id="test_app">
      <!--echarts的容器-->
	<div id="withdraw_chart" style="width: 100%;height: 520px; background:#fff"></div>
	<!-- <div id="announ_chart" style="width: 100%;height: 520px; background:#fff"></div> -->
  </div>
</template>
 
<script>
import * as echarts from 'echarts'
export default {
		name: 'withdrawNum',
		props: ['subdata'],	// 整个事件
		data() {
			return {
				charts: '',
				opinionData: this.subdata.withdraw_list  // 纵轴数据
				
			}
		},
		methods: {
			drawLine(id) {
				this.charts = echarts.init(document.getElementById(id))
				this.charts.setOption({
                    // title:{
                    //     left:'3%',
                    //     top:'5%',
                    //     // text:"最近一周订单数量",//标题文本，支持使用 \n 换行。
                    // },
					tooltip: {
						trigger: 'axis'
					},
					legend: {
                        align:'right',//文字在前图标在后
                        left:'5%',
                        top:'15%',
						data: ['withdrawal数量']
					},
					grid: {
                        top:'30%',
						left: '5%',
						right: '5%',
						bottom: '5%',
						containLabel: true
					},
 
					toolbox: {
						feature: {
							saveAsImage: {} //保存为图片
						}
					},
					xAxis: {
						// type: 'category',
                        boundaryGap:true,	// 距离坐标原点是否有间隙
                        axisTick:{
                            alignWithLabel:true //保证刻度线和标签对齐
                        },
						axisLabel: {
							interval: 30,			  // 间隔
							rotate: 0,                // 横坐标上label的倾斜度
      						// showMaxLabel: true,       // 显示最大刻度
      						showMinLabel: true,       // 显示最小刻度
						},
                        data: this.subdata.time_list   //x坐标的名称
					
					},
					yAxis: {
						type: 'value',
						boundaryGap: true,
                        // splitNumber:4, //纵坐标数
                        // interval:250 //强制设置坐标轴分割间隔
					},
 
					series: [{
						name: 'withdrawal数量',
						type: 'line', //折线图line;柱形图bar;饼图pie
						// stack: '总量',

                        areaStyle: {
                            //显示区域颜色---渐变效果
                            color:{
                                type: 'linear',
                                x: 0,
                                y: 0,
                                x2: 0,
                                y2: 1,
                                colorStops: [{
                                    offset: 0, color: 'rgb(100,100,249)' // 0% 处的颜色
                                }, {
                                    offset: 1, color: '#ffffff' // 100% 处的颜色
                                }],
                                global: false // 缺省为 false
                            }
                        },

                        itemStyle: {
							color: 'rgb(100,100,249)', //改变折线点的颜色
							lineStyle: {
								color: 'rgb(100,100,249)' //改变折线颜色
							}
                            
                        },
						
						// data: this.subdata.opinionData
						data: this.subdata.withdraw_list
					}],
				})
			}
		},
		//调用
		mounted() {
			this.$nextTick(function() {
				// console.log(this.subdata.time_list)
				// console.log(this.subdata.announ_list)
				this.drawLine('withdraw_chart')
			})
		}
	}
</script>
 
<style scoped>
	* {
		margin: 0;
		padding: 0;
		list-style: none;
	}
</style>