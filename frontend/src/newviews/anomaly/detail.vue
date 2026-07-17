<template>
	<div class="app-container">
		<el-dialog v-if="this.canJudge" v-model="judgeDialogVisible" title="研判事件" width="30%">
			<el-form ref="judgeruleFormRef" :model="judgeruleForm" :rules="judgerules" label-width="80px" class="demo-ruleForm" size="default" status-icon>
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
					<el-button @click="judgeresetForm($refs.judgeruleFormRef)">取消</el-button>
					<el-button type="primary" @click="judgesubmitForm($refs.judgeruleFormRef)">确定</el-button>
				</span>
			</template>
		</el-dialog>

    <!-- 上半部分 -->
    <div class="basicblock">
      <div class="right">
        <div style="display: flex; width: 100%">
          <div style="font-weight: bold; font-size: 18px">{{ type }}事件</div>
          <div style="margin-left: auto">
            <el-button v-if="canJudge" size="default" type="primary" @click="handleJudge">研判</el-button>
            <el-button size="default" type="primary" @click="handleExportReport" :loading="exportLoading"> 导出报告 </el-button>
          </div>
        </div>
        <div style="display: flex; margin-top: 7px">
          <el-table :data="tableData" border :show-header="false" :cell-style="cellStyle" :span-method="arraySpanMethod">
            <el-table-column align="center" prop="prop1" label="" />
            <el-table-column prop="prop2" label="">
              <template #default="scope">
                <template v-if="type === '路由泄漏' && scope.$index === tableData.length-2">
                <span v-for="(item,index) in scope.row.prop2" :key="index">
                  <span :style="{color:(item === event.leak_by ? '#d41a1a' : item === event.leak_to ?  '#ff6a00' : item === event.attacked_as ? '#1ad45b' : '#000')}">{{ item }}</span>
                  <span v-if="index !== scope.row.prop2.length - 1">{{ '<-' }}</span>
                </span>
                </template>
              </template>
            </el-table-column>
            <el-table-column align="center" prop="prop3" label="" />
            <el-table-column prop="prop4" label="" />
          </el-table>
        </div>
      </div>
    </div>


    <!-- 生成下半部分 -->
		<div class="block">
			<ul class="tabs">
				<li v-for="(value, key) in (type === '边界中断'? tabs_components[1] : type === 'RPKI证书异常'? tabs_components[2] : type === '子前缀劫持'? tabs_components[3] : type === '国家中断'? tabs_components[4] : type === '前缀中断' || type === '前缀劫持' ? tabs_components[5] : tabs_components[0])" :key="key" @click="select(value)">
					<a href="javaScript:void(0)" :class="{ active: isSelectedComponent === value }">{{ key }}</a>
				</li>
			</ul>
			<div class="tab-content">
				<!-- 国家时序特征图 -->
				<div v-show="isCountryFeatureTab(isSelectedComponent)" class="country-feature-container">
					<div class="chart-controls">
						<el-date-picker
							v-model="countryFeatureTimeRange"
							type="datetimerange"
							range-separator="至"
							start-placeholder="开始时间"
							end-placeholder="结束时间"
							size="default"
							style="max-width: 380px;"
						/>
						<el-button
							size="default"
							type="primary"
							@click="fetchCountryFeatureData"
							:loading="countryFeatureLoading"
						>
							重新查询
						</el-button>
						<el-button
							size="default"
							@click="exportCountryFeatureChart"
							:disabled="countryFeatureLoading"
						>
							导出图片
						</el-button>
					</div>
					<div class="chart-wrapper" v-loading="countryFeatureLoading">
						<FeatureChart
							v-if="countryFeatureChartType === 'feature'"
							:data="countryFeatureData"
							:title="getCountryFeatureChartTitle()"
							:loading="countryFeatureLoading"
							ref="countryFeatureChartRef"
						/>
						<AsOutageChart
							v-else-if="countryFeatureChartType === 'as-outage'"
							:data="countryAsOutageData"
							:title="getCountryFeatureChartTitle()"
							:loading="countryFeatureLoading"
							ref="countryAsOutageChartRef"
						/>
						<PrefixOutageChart
							v-else-if="countryFeatureChartType === 'prefix-outage'"
							:data="countryPrefixOutageData"
							:title="getCountryFeatureChartTitle()"
							:loading="countryFeatureLoading"
							ref="countryPrefixOutageChartRef"
						/>
						<ResourceChart
							v-else-if="countryFeatureChartType === 'resource'"
							:data="countryResourceData"
							:title="getCountryFeatureChartTitle()"
							:loading="countryFeatureLoading"
							ref="countryResourceChartRef"
						/>
					</div>
				</div>
				<!-- 其他组件 -->
				<div v-show="!isCountryFeatureTab(isSelectedComponent)">
					<keep-alive>
						<component v-bind:is="isSelectedComponent" :subdata="event" :type="type"></component>
					</keep-alive>
				</div>
			</div>
		</div>
	</div>
</template>

<script>
import axios from 'axios';
import PrefixInfo from '/@/components/RouteMonitor/PrefixInfo.vue';
import EventReplay from '/@/components/RouteMonitor/EventReplay.vue';
import announceNum from '/@/components/RouteMonitor/AnnounNum.vue';
import withdrawNum from '/@/components/RouteMonitor/WithdrawNum.vue';
import ContactInfo from '/@/components/RouteMonitor/ContactInfo.vue';
import CertificateChain from "/@/components/RouteMonitor/CertificateChain.vue";
import EventRecover from "/@/components/RouteMonitor/EventRecover.vue"
import FeatureChart from '/@/components/feature/FeatureChart.vue';
import AsOutageChart from '/@/components/feature/AsOutageChart.vue';
import PrefixOutageChart from '/@/components/feature/PrefixOutageChart.vue';
import ResourceChart from '/@/components/feature/ResourceChart.vue';
import {ElMessage} from 'element-plus';
import request from '/@/utils/request';
import baseUrl from "/@/api";

export default {
	name: 'anomalyDetail',
	components: {
		announceNum,        // 宣告数量
		withdrawNum,        // 回撤数量
		EventReplay,        // 事件恢复
		PrefixInfo,         //  重要服务
    ContactInfo,        //  联系人信息
    CertificateChain,   //  
    EventRecover,       // 事件恢复
		FeatureChart,       // Feature时序图
		AsOutageChart,      // AS中断时序图
		PrefixOutageChart,  // Prefix中断时序图
		ResourceChart,      // IP资源时序图
	},
	data() {
		return {
      // server: 'http://10.3.242.226:19746/', // 对应flask服务的ip地址和端口
			server: baseUrl + '', // 对应flask服务的ip地址和端口
			event: {},
			type: '',
			flag: true,
      tabs_components: [
        { 事件回放: 'EventReplay', 宣告数量: 'announceNum', 回撤数量: 'withdrawNum', 重要服务: 'PrefixInfo', 联系人信息: 'ContactInfo' },
        { 事件回放: 'EventReplay', 联系人信息: 'ContactInfo' },
        { 证书链条: 'CertificateChain' },
        { 事件回放: 'EventReplay', 重要服务: 'PrefixInfo', 联系人信息: 'ContactInfo' },
        {
          国家拓扑: 'EventReplay',
          AS列表: 'PrefixInfo',
          Feature时序图: 'CountryFeatureFeature',
          AS中断时序图: 'CountryFeatureAsOutage',
          Prefix中断时序图: 'CountryFeaturePrefixOutage',
          IP资源时序图: 'CountryFeatureResource',
        },
        { 事件回放: 'EventReplay', 事件恢复: 'EventRecover', 宣告数量: 'announceNum', 回撤数量: 'withdrawNum', 重要服务: 'PrefixInfo', 联系人信息: 'ContactInfo' },
      ], // key显示在前端，value为子组件名
			isSelectedComponent: 'EventReplay',
			judgeDialogVisible: false,
			judgeruleForm: {
				detail_url: '',
				check_list: [],
				input_reason: '',
				judge_result: '',
			},
			judgerules: {
				input_reason: [{ validator: this.validateInputReason, trigger: 'blur' }],
				judge_result: [{ required: true, message: '研判结论不能为空', trigger: 'change' }],
			},
			canJudge: false,
      tableData: [],
			exportLoading: false, // 导出报告加载状态
			// 国家时序特征图相关状态
			countryFeatureChartType: 'feature', // feature | as-outage | prefix-outage | resource
			countryFeatureLoading: false,
			countryFeatureTimeRange: ['', ''], // 时间范围选择
			countryFeatureData: [],
			countryAsOutageData: [],
			countryPrefixOutageData: [],
			countryResourceData: [],
		};
	},
	mounted() {
    this.getCanJudge();
	},
  created() {
    console.log("start created")
		this.type = this.$route.query.type; // 事件类型
		this.judgeruleForm.detail_url = this.$route.query.detail_url;

    this.isSelectedComponent = (this.type === 'RPKI证书异常'? 'CertificateChain': 'EventReplay')

    console.log(this.isSelectedComponent)
    console.log(this.$route.query.detail_url)

    if(this.type === 'RPKI证书异常'){
      request({
        url: 'http://10.3.242.224:8070/sys/exportcerDetail',
        // url: 'http://your-rpki-host/sys/exportcerDetail',
        headers: {
          "Content-Type": "application/json",
        },
        method: 'post',
        data: {
          "id": parseInt(this.judgeruleForm.detail_url.split('/')[1])
        },
      }).then((res) => {
        this.event = res;
        console.log(this.event)
        this.flag = true;
        this.initTableData()
      })

    }
    else {
      console.log("发送请求")
      // 从后端获取详情数据
      axios({
        methods: 'GET',
        url: this.server + this.$route.query.detail_url,
      }).then((res) => {
        console.log(res)
        // ?为可选链操作符，解决 Cannot read properties of undefined类型的报错
        const event = res?.data; // 返回的事件信息
        const levelMap = {
          low: '低危事件',
          middle: '中危事件',
          high: '高危事件',
        };
        console.log("请求返回")
      
        if (event.hasOwnProperty('event_level')) {
          event.event_level = levelMap[event.event_level];
        }
        this.event = event;
        this.flag = true;
        this.initTableData()
        console.log("created")
      });

    }
	},
	methods: {
    arraySpanMethod ({ row, column, rowIndex, columnIndex, }) {
      if ((rowIndex === 0 && this.type !== 'RPKI证书异常') || (rowIndex === this.tableData.length-2 && this.type === '路由泄漏') || rowIndex === this.tableData.length-1) {
        if (columnIndex === 0) {
          return [1, 1]
        }
        else if(columnIndex === 1){
          return [1, 4]
        }
        else{
          return [0, 0]
        }
      }
    },
		isCountryFeatureTab(value) {
			return [
				'CountryFeatureFeature',
				'CountryFeatureAsOutage',
				'CountryFeaturePrefixOutage',
				'CountryFeatureResource',
			].includes(value);
		},
		getCountryFeatureChartTypeByTab(value) {
			switch (value) {
				case 'CountryFeatureAsOutage':
					return 'as-outage';
				case 'CountryFeaturePrefixOutage':
					return 'prefix-outage';
				case 'CountryFeatureResource':
					return 'resource';
				default:
					return 'feature';
			}
		},
    // 数组去重
    removeDuplicate(arr) {
      const map = new Map()
      const newArr = []

      arr.forEach(item => {
        if (!map.has(item)) { // has()用于判断map是否包为item的属性值
          map.set(item, true) // 使用set()将item设置到map中，并设置其属性值为true
          newArr.push(item)
        }
      })

      return newArr
    },
    cellStyle({ row, column, rowIndex, columnIndex }) {
      if(columnIndex % 2 === 0){
        return { fontWeight: 'border', backgroundColor: 'rgba(237,240,252,0.4)' };
      }
    },
    initTableData() {
      if(this.type === '前缀劫持'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '受害方AS号',
            prop2: this.event.hasOwnProperty('attacked_as') ? this.event.attacked_as : 'no data',
            prop3: '肇事方AS号',
            prop4: this.event.hasOwnProperty('attacker_as') ? this.event.attacker_as : 'no data',
          },
          {
            prop1: '受害方AS名称',
            prop2: this.event.hasOwnProperty('attacked_as_name') ? this.event.attacked_as_name : 'no data',
            prop3: '肇事方AS名称',
            prop4: this.event.hasOwnProperty('attacker_as_name') ? this.event.attacker_as_name : 'no data',
          },
          {
            prop1: '受害方机构',
            prop2: this.event.hasOwnProperty('attacked_org') ? this.event.attacked_org : 'no data',
            prop3: '肇事方机构',
            prop4: this.event.hasOwnProperty('attacker_org') ? this.event.attacker_org : 'no data',
          },
          {
            prop1: '受害方国家',
            prop2: this.event.hasOwnProperty('attacked_country') ? this.event.attacked_country : 'no data',
            prop3: '肇事方国家',
            prop4: this.event.hasOwnProperty('attacker_country') ? this.event.attacker_country : 'no data',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === '子前缀劫持'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '受害方AS号',
            prop2: this.event.hasOwnProperty('attacked_as') ? this.event.attacked_as : 'no data',
            prop3: '肇事方AS号',
            prop4: this.event.hasOwnProperty('attacker_as') ? this.event.attacker_as : 'no data',
          },
          {
            prop1: '受害方AS名称',
            prop2: this.event.hasOwnProperty('attacked_as_name') ? this.event.attacked_as_name : 'no data',
            prop3: '肇事方AS名称',
            prop4: this.event.hasOwnProperty('attacker_as_name') ? this.event.attacker_as_name : 'no data',
          },
          {
            prop1: '受害方机构',
            prop2: this.event.hasOwnProperty('attacked_org') ? this.event.attacked_org : 'no data',
            prop3: '肇事方机构',
            prop4: this.event.hasOwnProperty('attacker_org') ? this.event.attacker_org : 'no data',
          },
          {
            prop1: '受害方国家',
            prop2: this.event.hasOwnProperty('attacked_country') ? this.event.attacked_country : 'no data',
            prop3: '肇事方国家',
            prop4: this.event.hasOwnProperty('attacker_country') ? this.event.attacker_country : 'no data',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === '前缀中断'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '受害方AS号',
            prop2: this.event.hasOwnProperty('attacked_as') ? this.event.attacked_as : 'no data',
            prop3: '肇事方AS号',
            prop4: this.event.hasOwnProperty('attacker_as') ? this.event.attacker_as : 'no data',
          },
          {
            prop1: '受害方AS名称',
            prop2: this.event.hasOwnProperty('attacked_as_name') ? this.event.attacked_as_name : 'no data',
            prop3: '肇事方AS名称',
            prop4: this.event.hasOwnProperty('attacker_as_name') ? this.event.attacker_as_name : 'no data',
          },
          {
            prop1: '受害方机构',
            prop2: this.event.hasOwnProperty('attacked_org') ? this.event.attacked_org : 'no data',
            prop3: '肇事方机构',
            prop4: this.event.hasOwnProperty('attacker_org') ? this.event.attacker_org : 'no data',
          },
          {
            prop1: '受害方国家',
            prop2: this.event.hasOwnProperty('attacked_country') ? this.event.attacked_country : 'no data',
            prop3: '肇事方国家',
            prop4: this.event.hasOwnProperty('attacker_country') ? this.event.attacker_country : 'no data',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === 'AS中断'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '受害方AS号',
            prop2: this.event.hasOwnProperty('attacked_as') ? this.event.attacked_as : 'no data',
            prop3: '肇事方AS号',
            prop4: this.event.hasOwnProperty('attacker_as') ? this.event.attacker_as : 'no data',
          },
          {
            prop1: '受害方AS名称',
            prop2: this.event.hasOwnProperty('attacked_as_name') ? this.event.attacked_as_name : 'no data',
            prop3: '肇事方AS名称',
            prop4: this.event.hasOwnProperty('attacker_as_name') ? this.event.attacker_as_name : 'no data',
          },
          {
            prop1: '受害方机构',
            prop2: this.event.hasOwnProperty('attacked_org') ? this.event.attacked_org : 'no data',
            prop3: '肇事方机构',
            prop4: this.event.hasOwnProperty('attacker_org') ? this.event.attacker_org : 'no data',
          },
          {
            prop1: '受害方国家',
            prop2: this.event.hasOwnProperty('attacked_country') ? this.event.attacked_country : 'no data',
            prop3: '肇事方国家',
            prop4: this.event.hasOwnProperty('attacker_country') ? this.event.attacker_country : 'no data',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === '国家中断'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === '边界中断'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '出口AS号',
            prop2: this.event.hasOwnProperty('export_as') ? this.event.export_as : 'no data',
            prop3: '出口下一跳AS号',
            prop4: this.event.hasOwnProperty('peer_as') ? this.event.peer_as : 'no data',
          },
          {
            prop1: '出口AS名称',
            prop2: this.event.hasOwnProperty('export_as_name') ? this.event.export_as_name : 'no data',
            prop3: '出口下一跳AS名称',
            prop4: this.event.hasOwnProperty('peer_as_name') ? this.event.peer_as_name : 'no data',
          },
          {
            prop1: '出口机构',
            prop2: this.event.hasOwnProperty('export_as_org') ? this.event.export_as_org : 'no data',
            prop3: '出口下一跳机构',
            prop4: this.event.hasOwnProperty('peer_as_org') ? this.event.peer_as_org : 'no data',
          },
          {
            prop1: '出口国家',
            prop2: this.event.hasOwnProperty('export_as_country') ? this.event.export_as_country : 'no data',
            prop3: '出口下一跳国家',
            prop4: this.event.hasOwnProperty('peer_as_country') ? this.event.peer_as_country : 'no data',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === '路由泄漏'){
        this.tableData = [
          {
            prop1: '事件描述',
            prop2: this.event.hasOwnProperty('event_info') ? this.event.event_info : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '受害方AS号',
            prop2: this.event.hasOwnProperty('attacked_as') ? this.event.attacked_as : 'no data',
            prop3: '肇事方AS号',
            prop4: this.event.hasOwnProperty('attacker_as') ? this.event.attacker_as : 'no data',
          },
          {
            prop1: '受害方AS名称',
            prop2: this.event.hasOwnProperty('attacked_as_name') ? this.event.attacked_as_name : 'no data',
            prop3: '肇事方AS名称',
            prop4: this.event.hasOwnProperty('attacker_as_name') ? this.event.attacker_as_name : 'no data',
          },
          {
            prop1: '受害方机构',
            prop2: this.event.hasOwnProperty('attacked_org') ? this.event.attacked_org : 'no data',
            prop3: '肇事方机构',
            prop4: this.event.hasOwnProperty('attacker_org') ? this.event.attacker_org : 'no data',
          },
          {
            prop1: '受害方国家',
            prop2: this.event.hasOwnProperty('attacked_country') ? this.event.attacked_country : 'no data',
            prop3: '肇事方国家',
            prop4: this.event.hasOwnProperty('attacker_country') ? this.event.attacker_country : 'no data',
          },
          {
            prop1: 'AS路径',
            prop2: this.event.hasOwnProperty('as_path') ? this.removeDuplicate(this.event.as_path.split(" ")) : 'no data',
            prop3: '',
            prop4: '',
          },
          {
            prop1: '等级描述',
            prop2: this.event.hasOwnProperty('event_descr') ? this.event.event_descr : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
      else if(this.type === 'RPKI证书异常'){
        this.tableData = [
          {
            prop1: 'ASNs',
            prop2: this.event.jsonAll.hasOwnProperty('asnModel') ? this.event.jsonAll.asnModel.asns.map((item) => item.asn).join(',') : 'no data',
            prop3: 'IPs',
            prop4: this.event.jsonAll.hasOwnProperty('cerIpAddressModel') ? this.event.jsonAll.cerIpAddressModel.cerIpAddresses.map((item) => item.addressPrefix).join(',') : 'no data',
          },
          {
            prop1: 'Validity',
            prop2: this.event.jsonAll.hasOwnProperty('notBefore') && this.event.jsonAll.hasOwnProperty('notAfter') ? this.event.jsonAll.notBefore + ' - ' + this.event.jsonAll.notAfter : 'no data',
            prop3: 'Trust Anchor',
            prop4: this.event.origin.hasOwnProperty('rir') ? this.event.origin.rir : 'no data',
          },
          {
            prop1: 'Name',
            prop2: this.event.jsonAll.hasOwnProperty('subject') ? this.event.jsonAll.subject : 'no data',
            prop3: 'Key',
            prop4: this.event.jsonAll.hasOwnProperty('ski') ? this.event.jsonAll.ski : 'no data',
          },
          {
            prop1: 'Parent Key',
            prop2: this.event.jsonAll.hasOwnProperty('aki') ? this.event.jsonAll.aki : 'no data',
            prop3: 'Path',
            prop4: this.event.jsonAll.hasOwnProperty('aiaModel') ? this.event.jsonAll.aiaModel.caIssuers : 'no data',
          },
          {
            prop1: 'Subject Information Access(SIA)',
            prop2: this.event.jsonAll.hasOwnProperty('siaModel') ? this.event.jsonAll.siaModel.caRepository + ',\n' + this.event.jsonAll.siaModel.rpkiNotify + ',\n' + this.event.jsonAll.siaModel.rpkiManifest : 'no data',
            prop3: '',
            prop4: '',
          },
        ]
      }
    },
		select(value) {
			this.isSelectedComponent = value;
			// 如果选中国家中断时序特征图Tab，初始化时间范围并加载数据
			if (this.type === '国家中断' && this.isCountryFeatureTab(value)) {
				this.countryFeatureChartType = this.getCountryFeatureChartTypeByTab(value);
				this.initCountryFeatureTimeRange();
				this.fetchCountryFeatureData();
			}
		},
		judgeresetForm(formEl) {
			if (!formEl) return;
			formEl.resetFields();
			this.judgeDialogVisible = false;
		},
		async judgesubmitForm(formEl) {
			if (!formEl) return;
			await formEl.validate((valid, fields) => {
				if (valid) {
					this.judge().then(() => {
						formEl.resetFields();
					});
					this.judgeDialogVisible = false;
				} else {
					console.log('error submit!', fields);
				}
			});
		},
		validateInputReason(rule, value, callback) {
			if (value.trim() === '' && this.judgeruleForm.check_list.length === 0) {
				callback(new Error('研判依据不能为空'));
			} else {
				callback();
			}
		},
		getJudgeReason() {
			let { check_list, input_reason } = this.judgeruleForm;
			input_reason = input_reason.trim();
			if (input_reason !== '') {
				check_list.push(input_reason);
			}
			return check_list.join('；');
		},
		async judge() {
			const judgeResult = await request({
        // url: 'http://10.3.242.226:19746/judge',
				url: baseUrl + 'events/judge',
				method: 'post',
				data: {
					detail_url: this.judgeruleForm.detail_url,
					judge_reason: this.getJudgeReason(),
					state: this.judgeruleForm.judge_result,
				},
			});
			if (judgeResult.status) {
				ElMessage.success('事件研判成功');
				this.getCanJudge();
			} else {
				const msg = judgeResult.msg ? judgeResult.msg : '事件研判失败';
				ElMessage.error(msg);
			}
		},
		handleJudge() {
			this.judgeDialogVisible = true;
		},
		// 是否展示研判按钮
		async getCanJudge() {
			const user_res = await request({
        // url: 'http://10.3.242.226:19746/profile',
				url: baseUrl + 'profile',
				method: 'get',
			});
			if (user_res.status && user_res.role !== 'guest') {
				// 判断当前事件的状态
				const state_res = await request({
          // url: 'http://10.3.242.226:19746/event_state',
					url: baseUrl + 'events/state',
					method: 'get',
					params: {
						detail_url: this.judgeruleForm.detail_url,
					},
				});
				if (state_res.status && (state_res.state === 'judge' || state_res.state === 'suspected')) {
					this.canJudge = true;
					return;
				}
			}
			this.canJudge = false;
		},

		// ========== 导出报告相关方法 ==========
		async handleExportReport() {
			this.exportLoading = true;
			try {
				const detail_url = this.judgeruleForm.detail_url;
				
				// 国家中断事件使用新的导出接口
				if (this.type === '国家中断') {
					await this.exportCountryOutageReport(detail_url);
				} else {
					// 其他事件类型使用原有的导出接口
					await this.exportGeneralReport(detail_url);
				}
			} catch (error) {
				console.error('导出报告失败:', error);
				ElMessage.error('导出报告失败，请稍后重试');
			} finally {
				this.exportLoading = false;
			}
		},

		// 导出国家中断报告
		async exportCountryOutageReport(detail_url) {
			// detail_url 格式: country_outage/2026-01-09 19:45:00/IR/1/r
			const parts = detail_url.split('/');
			if (parts.length < 5) {
				ElMessage.error('事件URL格式错误');
				return;
			}
			
			const start_time = parts[1];
			const country = parts[2];
			const event_id = parts[3];
			const source = parts[4];
			
			// 调用后端导出接口
			const url = `${baseUrl}reports/country-outage-export/${country}/${encodeURIComponent(start_time)}/${event_id}/${source}`;
			
			try {
				const response = await axios({
					method: 'GET',
					url: url,
					responseType: 'blob',
					timeout: 60000,
				});
				
				// 创建下载链接
				const blob = new Blob([response.data], { 
					type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document' 
				});
				const downloadUrl = window.URL.createObjectURL(blob);
				const link = document.createElement('a');
				link.href = downloadUrl;
				
				// 从响应头获取文件名，或使用默认名称
				const contentDisposition = response.headers['content-disposition'];
				let fileName = `国家中断报告_${country}_${start_time.split(' ')[0]}.docx`;
				if (contentDisposition) {
					const fileNameMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/);
					if (fileNameMatch && fileNameMatch[1]) {
						fileName = decodeURIComponent(fileNameMatch[1].replace(/['"]/g, ''));
					}
				}
				
				link.download = fileName;
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);
				window.URL.revokeObjectURL(downloadUrl);
				
				ElMessage.success('报告导出成功');
			} catch (error) {
				console.error('导出国家中断报告失败:', error);
				if (error.response && error.response.data) {
					// 尝试读取错误信息
					const reader = new FileReader();
					reader.onload = () => {
						try {
							const errorData = JSON.parse(reader.result);
							ElMessage.error(errorData.msg || '导出失败');
						} catch (e) {
							ElMessage.error('导出失败');
						}
					};
					reader.readAsText(error.response.data);
				} else {
					ElMessage.error('导出失败，请稍后重试');
				}
			}
		},

		// 导出通用报告（其他事件类型）
		async exportGeneralReport(detail_url) {
			try {
				// 不能用 request()：它的响应拦截器会把 blob 当成普通 data 处理
				const response = await axios({
					method: 'POST',
					url: baseUrl + 'reports/word-export',
					data: { detail_url },
					responseType: 'blob',
					timeout: 60000,
				});

				const blob = new Blob([response.data], {
					type: 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
				});
				const downloadUrl = window.URL.createObjectURL(blob);
				const link = document.createElement('a');
				link.href = downloadUrl;
				link.download = `事件报告_${this.type}.docx`;
				document.body.appendChild(link);
				link.click();
				document.body.removeChild(link);
				window.URL.revokeObjectURL(downloadUrl);
				ElMessage.success('报告导出成功');
			} catch (error) {
				console.error('导出报告失败:', error);
				ElMessage.error('导出失败，请稍后重试');
			}
		},

		// ========== 国家时序特征图相关方法 ==========
		// 初始化时间范围
		initCountryFeatureTimeRange() {
			// 如果时间范围已经设置过，不再重复设置
			if (this.countryFeatureTimeRange[0] && this.countryFeatureTimeRange[1]) {
				return;
			}
			
			// 从事件数据中获取时间范围
			if (this.event && this.event.start_time) {
				const startDate = this.parseEventTime(this.event.start_time);
				const endDate = this.parseEventTime(this.event.end_time || this.event.start_time);

				if (startDate) {
					const startMinusOneDay = new Date(startDate.getTime() - 24 * 60 * 60 * 1000);
					const endPlusOneDay = new Date(startDate.getTime() + 24 * 60 * 60 * 1000);
					const startTime = this.formatDateTime(startMinusOneDay);
					const endTime = this.formatDateTime(endPlusOneDay);
					// const endTime = this.formatDateTime(endDate || startDate);
					this.countryFeatureTimeRange = [startTime, endTime];
				}
			}
		},

		// 处理图表类型变化
		handleCountryFeatureChartTypeChange() {
			// 清空当前数据
			this.countryFeatureData = [];
			this.countryAsOutageData = [];
			this.countryPrefixOutageData = [];
			this.countryResourceData = [];
			// 重新获取数据
			this.fetchCountryFeatureData();
		},

		// 获取国家时序特征数据
		async fetchCountryFeatureData() {
			if (!this.event || !this.event.attacked_country) {
				ElMessage.warning('无法获取国家信息');
				return;
			}

			// 从时间选择器获取时间范围
			if (!this.countryFeatureTimeRange || !this.countryFeatureTimeRange[0] || !this.countryFeatureTimeRange[1]) {
				ElMessage.warning('请选择时间范围');
				return;
			}

			const country = this.event.attacked_country;
			// 格式化时间
			const startTime = this.formatDateTime(new Date(this.countryFeatureTimeRange[0]));
			const endTime = this.formatDateTime(new Date(this.countryFeatureTimeRange[1]));

			this.countryFeatureLoading = true;

			try {
				switch (this.countryFeatureChartType) {
					case 'feature':
						await this.fetchCountryFeature(country, startTime, endTime);
						break;
					case 'as-outage':
						await this.fetchCountryAsOutage(country, startTime, endTime);
						break;
					case 'prefix-outage':
						await this.fetchCountryPrefixOutage(country, startTime, endTime);
						break;
					case 'resource':
						await this.fetchCountryResource(country, startTime, endTime);
						break;
				}
			} catch (error) {
				console.error('获取数据失败:', error);
				ElMessage.error('数据获取失败，请稍后重试');
			} finally {
				this.countryFeatureLoading = false;
			}
		},

		// 获取Feature数据
		async fetchCountryFeature(country, startTime, endTime) {
			const response = await request({
				url: `${baseUrl}features/countries`,
				method: 'get',
				params: {
					country: country,
					start_time: startTime,
					end_time: endTime,
					page_num: 1,
					page_size: 1
				},
				timeout: 500000
			});

			if (response && response.data && Array.isArray(response.data) && response.data.length > 0) {
				const firstItem = response.data[0];
				if (firstItem && firstItem.time_series_data && Array.isArray(firstItem.time_series_data)) {
					this.countryFeatureData = firstItem.time_series_data;
					if (this.countryFeatureData.length === 0) {
						ElMessage.warning(`${country}在指定时间范围内暂无Feature数据`);
					}
				} else {
					this.countryFeatureData = [];
					ElMessage.warning('Feature数据格式异常');
				}
			} else {
				this.countryFeatureData = [];
				ElMessage.warning(`未找到${country}的Feature数据`);
			}
		},

		// 获取AS中断数据
		async fetchCountryAsOutage(country, startTime, endTime) {
			const response = await request({
				url: `${baseUrl}features/outages/country-as`,
				method: 'get',
				params: {
					country: country,
					start_time: startTime,
					end_time: endTime
				},
				timeout: 500000
			});

			let data = null;
			if (response && Array.isArray(response)) {
				data = response;
			} else if (response && response.data && Array.isArray(response.data)) {
				data = response.data;
			}

			if (data && Array.isArray(data)) {
				this.countryAsOutageData = data;
				if (this.countryAsOutageData.length === 0) {
					ElMessage.warning(`${country}在指定时间范围内暂无AS中断数据`);
				}
			} else {
				this.countryAsOutageData = [];
				ElMessage.warning(`未找到${country}的AS中断数据`);
			}
		},

		// 获取Prefix中断数据
		async fetchCountryPrefixOutage(country, startTime, endTime) {
			const response = await request({
				url: `${baseUrl}features/outages/country-prefix`,
				method: 'get',
				params: {
					country: country,
					start_time: startTime,
					end_time: endTime
				},
				timeout: 500000
			});

			let data = null;
			if (response && Array.isArray(response)) {
				data = response;
			} else if (response && response.data && Array.isArray(response.data)) {
				data = response.data;
			}

			if (data && Array.isArray(data)) {
				this.countryPrefixOutageData = data;
				if (this.countryPrefixOutageData.length === 0) {
					ElMessage.warning(`${country}在指定时间范围内暂无Prefix中断数据`);
				}
			} else {
				this.countryPrefixOutageData = [];
				ElMessage.warning(`未找到${country}的Prefix中断数据`);
			}
		},

		// 获取Resource数据
		async fetchCountryResource(country, startTime, endTime) {
			const response = await request({
				url: `${baseUrl}features/countries`,
				method: 'get',
				params: {
					country: country,
					start_time: startTime,
					end_time: endTime,
					page_num: 1,
					page_size: 1
				},
				timeout: 500000
			});

			if (response && response.data && Array.isArray(response.data) && response.data.length > 0) {
				const firstItem = response.data[0];
				if (firstItem && firstItem.time_series_data && Array.isArray(firstItem.time_series_data)) {
					this.countryResourceData = firstItem.time_series_data
						.filter(item => item.v4Prefix_num !== undefined || item.v6Prefix_num !== undefined || item.v4IP_num !== undefined)
						.map(item => ({
							time: item.time || item.t,
							v4Prefix_num: item.v4Prefix_num || 0,
							v6Prefix_num: item.v6Prefix_num || 0,
							v4IP_num: item.v4IP_num || 0
						}));
					
					if (this.countryResourceData.length === 0) {
						ElMessage.warning(`${country}在指定时间范围内暂无IP资源数据`);
					}
				} else {
					this.countryResourceData = [];
					ElMessage.warning('Resource数据格式异常');
				}
			} else {
				this.countryResourceData = [];
				ElMessage.warning(`未找到${country}的IP资源数据`);
			}
		},

		// 导出图表
		exportCountryFeatureChart() {
			let chartRef = null;
			let fileName = '';
			const country = this.event.attacked_country || '未知国家';

			switch (this.countryFeatureChartType) {
				case 'feature':
					chartRef = this.$refs.countryFeatureChartRef;
					fileName = `${country}_Feature时序图.png`;
					break;
				case 'as-outage':
					chartRef = this.$refs.countryAsOutageChartRef;
					fileName = `${country}_AS中断时序图.png`;
					break;
				case 'prefix-outage':
					chartRef = this.$refs.countryPrefixOutageChartRef;
					fileName = `${country}_Prefix中断时序图.png`;
					break;
				case 'resource':
					chartRef = this.$refs.countryResourceChartRef;
					fileName = `${country}_IP资源时序图.png`;
					break;
			}

			if (!chartRef) {
				ElMessage.error('图表未初始化');
				return;
			}

			try {
				const dataURL = chartRef.exportChart();
				if (dataURL) {
					const link = document.createElement('a');
					link.download = fileName;
					link.href = dataURL;
					link.click();
					ElMessage.success('图片导出成功');
				}
			} catch (error) {
				console.error('导出失败:', error);
				ElMessage.error('导出失败');
			}
		},

		// 获取图表标题
		getCountryFeatureChartTitle() {
			const country = this.event.attacked_country || '未知国家';
			let typeText = '';
			switch (this.countryFeatureChartType) {
				case 'feature':
					typeText = '报文时序图';
					break;
				case 'as-outage':
					typeText = 'AS中断事件时序图';
					break;
				case 'prefix-outage':
					typeText = 'Prefix中断时序图';
					break;
				case 'resource':
					typeText = 'IP资源时序图';
					break;
			}
			return `${country} 国家${typeText}`;
		},

		// 格式化日期时间为 YYYY-MM-DD HH:MM:SS
		formatDateTime(date) {
			if (!date) return '';
			const Y = date.getFullYear();
			const M = (date.getMonth() + 1).toString().padStart(2, '0');
			const D = date.getDate().toString().padStart(2, '0');
			const h = date.getHours().toString().padStart(2, '0');
			const m = date.getMinutes().toString().padStart(2, '0');
			const s = date.getSeconds().toString().padStart(2, '0');
			return `${Y}-${M}-${D} ${h}:${m}:${s}`;
		},

		// 格式化时间用于API调用
		formatTimeForApi(timeStr) {
			if (!timeStr) return '';
			
			// 如果是"2026年01月09日 11时20分55秒"格式
			if (timeStr.includes('年')) {
				const match = timeStr.match(/(\d+)年(\d+)月(\d+)日\s+(\d+)时(\d+)分(\d+)秒/);
				if (match) {
					return `${match[1]}-${match[2]}-${match[3]} ${match[4]}:${match[5]}:${match[6]}`;
				}
			}
			
			// 如果已经是标准格式，直接返回
			return timeStr;
		},

		// 解析事件时间字符串为 Date
		parseEventTime(timeStr) {
			const formatted = this.formatTimeForApi(timeStr);
			if (!formatted) return null;
			const normalized = formatted.includes('T') ? formatted : formatted.replace(' ', 'T');
			const date = new Date(normalized);
			if (isNaN(date.getTime())) {
				return null;
			}
			return date;
		},
    
 
	},
};
</script>

<style scoped src="/@/assets/css/reset.css"></style>
<style lang="scss" scoped>
.app-container {
	background-color: #f0f0f0;
}
:deep(.el-table td.el-table__cell div){
  white-space: pre-wrap;
}
.basicblock {
	//height: 195px;
	padding: 30px 30px 20px 30px;
	display: flex;
	background: white;
	box-shadow: 0 0 6px rgb(230, 230, 230);
	.left {
		width: 15%;
		text-align: center;
		.info {
			margin-top: 5px;
			font-size: 19px;
			line-height: 25px;
			white-space: nowrap; // 规定段落中的文本不进行换行
			text-overflow: ellipsis; // 显示省略号
			overflow: hidden;
		}
	}
	.right {
		width: 100%;
		text-align: left;
		// 后续表示级别的颜色改为css变量形式
		.line {
			display: flex;
			align-items: center;
			height: 30px;
		}
		span {
			display: inline-block;
			//width: 100%;
			white-space: nowrap; // 规定段落中的文本不进行换行
			text-overflow: ellipsis; // 显示省略号
			overflow: hidden; /* 内容超出宽度时隐藏超出部分的内容 */
		}
		.first_column {
			width: 100%;
			margin-right: 5%;
		}
		.second_column {
			width: 50%;
		}
	}
}
.block {
  padding: 10px 30px 20px 30px;
	.tabs {
		display: flex;
		margin: 0 auto;
		justify-content: left;
		border-bottom: rgb(230, 230, 230) solid 0.5px;
		//background: rgb(245, 248, 255);
		a {
			// 内联元素设置上下内边距/外边距无效
			display: inline-block;
			padding: 10px 20px;
			font-size: 16px;
			line-height: 21px;
		}
		.active {
			background: white;
			color: rgb(0, 0, 0);
			border: rgb(230, 230, 230) solid 0.5px;
			box-shadow: 0 -6px 6px -6px rgb(230, 230, 230), -6px 0 6px -6px rgb(230, 230, 230), 6px 0 6px -6px rgb(230, 230, 230);
			border-top-left-radius: 8px;
			border-top-right-radius: 8px;
			margin-bottom: -1px; // 导致父元素高度由底部缩减1像素,那么底边框位置向上抬升1px,正好与子元素的底边框重合,或者说进入了子元素的范围内,并且是被子元素压住了.
			background: #fff;
			border-bottom-color: transparent;
		}
	}
	.tab-content {
		background-color: #fff;
		border: rgb(230, 230, 230) solid 0.5px;
		border-top: transparent;
		// x轴平移 y轴偏移 模糊程度 扩展  Y为负为向上偏移
		//box-shadow: 6px 0 6px -6px rgb(230, 230, 230);
		box-shadow: 0 0 6px rgb(230, 230, 230);
		margin: 0 auto;
		height: 1200px;
	}
}

// 国家时序特征图样式
.country-feature-container {
	padding: 20px;
	height: 1160px; // 与tab-content保持一致的高度
	display: flex;
	flex-direction: column;

	.chart-controls {
		display: flex;
		align-items: center;
		flex-wrap: wrap; // 允许换行
		gap: 10px; // 元素间距
		margin-bottom: 20px;
		padding-bottom: 15px;
		border-bottom: 1px solid #e4e7ed;
	}

	.chart-wrapper {
		flex: 1;
		min-height: 500px;
		position: relative;
		overflow: hidden; // 防止内容溢出
	}
}
</style>
