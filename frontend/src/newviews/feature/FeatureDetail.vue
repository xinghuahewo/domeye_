<template>
  <div class="feature-detail-container">
    <!-- 标题和控制栏 -->
    <div class="detail-header">
      <div class="title-section">
        <h2 class="detail-title">
          <span v-if="detailType === 'country'">{{ targetName }} 国家时序特征</span>
          <span v-else>AS{{ targetName }} 时序特征</span>
        </h2>
        <div class="subtitle">{{ state.timeRange[0] }} 至 {{ state.timeRange[1] }}</div>
      </div>
      
      <div class="control-section">
        <!-- 图表类型选择 -->
        <el-select
          v-model="state.chartType"
          placeholder="请选择图表类型"
          size="default"
          style="max-width: 200px; margin-right: 10px;"
          @change="handleChartTypeChange"
        >
          <el-option
            v-for="option in chartTypeOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>
        
        <el-date-picker
          v-model="state.timeRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          size="default"
          style="max-width: 380px"
          class="mr10"
        />
        <el-button 
          size="default" 
          type="primary" 
          @click="handleQuery"
          :loading="state.loading"
          class="mr10"
        >
          重新查询
        </el-button>
        <el-button 
          size="default" 
          @click="exportChart"
          :disabled="state.loading || !hasValidData"
        >
          导出图片
        </el-button>
        <el-button
          v-if="detailType === 'country'"
          size="default"
          class="ml10"
          @click="exportCountryOutageData('as')"
          :loading="state.exportingAs"
          :disabled="state.loading"
        >
          导出AS中断明细
        </el-button>
        <el-button
          v-if="detailType === 'country'"
          size="default"
          class="ml10"
          @click="exportCountryOutageData('prefix')"
          :loading="state.exportingPrefix"
          :disabled="state.loading"
        >
          导出Prefix中断
        </el-button>
      </div>
    </div>

    <!-- 图表区域 -->
    <div class="chart-section">
      <!-- Feature时序图 -->
      <FeatureChart
        v-if="state.chartType === 'feature'"
        :data="state.featureData"
        :title="chartTitle"
        :loading="state.loading"
        ref="featureChartRef"
      />
      
      <!-- AS中断时序图 -->
      <AsOutageChart
        v-else-if="state.chartType === 'as-outage'"
        :data="state.asOutageData"
        :title="chartTitle"
        :loading="state.loading"
        ref="asOutageChartRef"
      />
      
      <!-- Prefix中断时序图 -->
      <PrefixOutageChart
        v-else-if="state.chartType === 'prefix-outage'"
        :data="state.prefixOutageData"
        :title="chartTitle"
        :loading="state.loading"
        ref="prefixOutageChartRef"
      />
      
      <!-- IP资源时序图 -->
      <ResourceChart
        v-else-if="state.chartType === 'resource'"
        :data="state.resourceData"
        :title="chartTitle"
        :loading="state.loading"
        ref="resourceChartRef"
      />
      
      <!-- 加载状态 -->
      <div v-if="state.loading" class="loading-overlay">
        <div class="loading-content">
          <el-icon class="is-loading loading-icon">
            <Loading />
          </el-icon>
          <div class="loading-text">正在获取{{ targetName }}的详细数据...</div>
          <div class="loading-subtitle">请稍候</div>
        </div>
      </div>
      
      <!-- 空数据状态 -->
      <div v-if="!state.loading && !hasValidData && state.hasQueried" class="empty-state">
        <el-empty description="暂无数据" />
        <div class="empty-tips">
          <div>可能的原因：</div>
          <div>• 该{{ detailType === 'country' ? '国家' : 'AS' }}在指定时间范围内没有相关数据</div>
          <div>• 时间范围设置过短，建议扩大查询范围</div>
          <div v-if="detailType === 'country'">• 国家名称可能不正确，请检查拼写</div>
          <div v-else>• ASN可能不存在或无数据</div>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import axios from 'axios';
import { ref, onMounted, onUnmounted, nextTick, reactive, computed } from 'vue';
import { useRoute } from 'vue-router';
import { Loading } from '@element-plus/icons-vue';
import request from "/@/utils/request";
import baseUrl from "/@/api";
import { ElMessage } from 'element-plus';
import FeatureChart from '/@/components/feature/FeatureChart.vue';
import AsOutageChart from '/@/components/feature/AsOutageChart.vue';
import PrefixOutageChart from '/@/components/feature/PrefixOutageChart.vue';
import ResourceChart from '/@/components/feature/ResourceChart.vue';

type RawFeaturePoint = {
  time?: string;
  t?: string;
  announce?: number;
  withdraw?: number;
  v4Prefix_num?: number;
  v6Prefix_num?: number;
  v4IP_num?: number;
};

type FeaturePoint = {
  time: string;
  announce: number;
  withdraw: number;
};

type ResourcePoint = {
  time: string;
  v4Prefix_num: number;
  v6Prefix_num: number;
  v4IP_num: number;
};

const FEATURE_INTERVAL_MS = 5 * 60 * 1000;

const route = useRoute();

// 从路由参数获取详情类型和目标
const detailType = ref<'country' | 'as'>('country');
const targetName = ref<string>('');

// 图表组件引用
const featureChartRef = ref<InstanceType<typeof FeatureChart> | null>(null);
const asOutageChartRef = ref<InstanceType<typeof AsOutageChart> | null>(null);
const prefixOutageChartRef = ref<InstanceType<typeof PrefixOutageChart> | null>(null);
const resourceChartRef = ref<any>(null);

const state = reactive({
  timeRange: ['', ''] as Array<string | Date>,
  chartType: 'feature' as 'feature' | 'as-outage' | 'prefix-outage' | 'resource',
  featureData: [] as { time?: string; t?: string; withdraw: number; announce: number }[],
  asOutageData: [] as { time_slot: string; outage_count: number }[],
  prefixOutageData: [] as { time_slot: string; outage_count: number }[],
  resourceData: [] as { time: string; v4Prefix_num: number; v6Prefix_num: number; v4IP_num: number }[],
  loading: false,
  exportingAs: false,
  exportingPrefix: false,
  error: null as string | null,
  hasQueried: false,
});

const parseDateTime = (value?: string): Date | null => {
  if (!value) return null;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const ceilToInterval = (timestamp: number, intervalMs: number): number => {
  return Math.ceil(timestamp / intervalMs) * intervalMs;
};

const floorToInterval = (timestamp: number, intervalMs: number): number => {
  return Math.floor(timestamp / intervalMs) * intervalMs;
};

const normalizeRawFeaturePoints = (data: RawFeaturePoint[]) => {
  return data
    .map((item) => {
      const date = parseDateTime(item.time || item.t);
      if (!date) return null;
      return {
        timestamp: date.getTime(),
        announce: Number(item.announce || 0),
        withdraw: Number(item.withdraw || 0),
        v4Prefix_num: Number(item.v4Prefix_num || 0),
        v6Prefix_num: Number(item.v6Prefix_num || 0),
        v4IP_num: Number(item.v4IP_num || 0),
      };
    })
    .filter((item): item is NonNullable<typeof item> => item !== null)
    .sort((a, b) => a.timestamp - b.timestamp);
};

const buildFeatureSeries = (data: RawFeaturePoint[], startTime: string, endTime: string): FeaturePoint[] => {
  const normalized = normalizeRawFeaturePoints(data);
  if (!normalized.length) return [];

  const startDate = parseDateTime(startTime);
  const endDate = parseDateTime(endTime);
  if (!startDate || !endDate) {
    return normalized.map((item) => ({
      time: formatDateTime(new Date(item.timestamp)),
      announce: item.announce,
      withdraw: item.withdraw,
    }));
  }

  const startSlot = ceilToInterval(startDate.getTime(), FEATURE_INTERVAL_MS);
  const endSlot = floorToInterval(endDate.getTime(), FEATURE_INTERVAL_MS);
  if (startSlot > endSlot) {
    return normalized.map((item) => ({
      time: formatDateTime(new Date(item.timestamp)),
      announce: item.announce,
      withdraw: item.withdraw,
    }));
  }

  const pointMap = new Map<number, FeaturePoint>();
  normalized.forEach((item) => {
    pointMap.set(item.timestamp, {
      time: formatDateTime(new Date(item.timestamp)),
      announce: item.announce,
      withdraw: item.withdraw,
    });
  });

  const filled: FeaturePoint[] = [];
  for (let slot = startSlot; slot <= endSlot; slot += FEATURE_INTERVAL_MS) {
    const point = pointMap.get(slot);
    if (point) {
      filled.push(point);
    } else {
      filled.push({
        time: formatDateTime(new Date(slot)),
        announce: 0,
        withdraw: 0,
      });
    }
  }

  return filled;
};

const buildResourceSeries = (data: RawFeaturePoint[], startTime: string, endTime: string): ResourcePoint[] => {
  const normalized = normalizeRawFeaturePoints(data);
  if (!normalized.length) return [];

  const startDate = parseDateTime(startTime);
  const endDate = parseDateTime(endTime);
  if (!startDate || !endDate) {
    return normalized.map((item) => ({
      time: formatDateTime(new Date(item.timestamp)),
      v4Prefix_num: item.v4Prefix_num,
      v6Prefix_num: item.v6Prefix_num,
      v4IP_num: item.v4IP_num,
    }));
  }

  const startSlot = ceilToInterval(startDate.getTime(), FEATURE_INTERVAL_MS);
  const endSlot = floorToInterval(endDate.getTime(), FEATURE_INTERVAL_MS);
  if (startSlot > endSlot) {
    return normalized.map((item) => ({
      time: formatDateTime(new Date(item.timestamp)),
      v4Prefix_num: item.v4Prefix_num,
      v6Prefix_num: item.v6Prefix_num,
      v4IP_num: item.v4IP_num,
    }));
  }

  const pointMap = new Map<number, ResourcePoint>();
  normalized.forEach((item) => {
    pointMap.set(item.timestamp, {
      time: formatDateTime(new Date(item.timestamp)),
      v4Prefix_num: item.v4Prefix_num,
      v6Prefix_num: item.v6Prefix_num,
      v4IP_num: item.v4IP_num,
    });
  });

  const filled: ResourcePoint[] = [];
  let lastKnown: ResourcePoint | null = null;
  for (let slot = startSlot; slot <= endSlot; slot += FEATURE_INTERVAL_MS) {
    const point = pointMap.get(slot);
    if (point) {
      lastKnown = point;
      filled.push(point);
    } else if (lastKnown) {
      filled.push({
        time: formatDateTime(new Date(slot)),
        v4Prefix_num: lastKnown.v4Prefix_num,
        v6Prefix_num: lastKnown.v6Prefix_num,
        v4IP_num: lastKnown.v4IP_num,
      });
    }
  }

  return filled;
};

// 图表类型选项
const chartTypeOptions = computed(() => {
  if (detailType.value === 'country') {
    return [
      { label: 'Feature时序图', value: 'feature' },
      { label: 'AS中断事件时序图', value: 'as-outage' },
      { label: 'Prefix中断时序图', value: 'prefix-outage' },
      { label: 'IP资源时序图', value: 'resource' }
    ];
  } else {
    return [
      { label: 'Feature时序图', value: 'feature' },
      { label: 'Prefix中断时序图', value: 'prefix-outage' },
      { label: 'IP资源时序图', value: 'resource' }
    ];
  }
});

// 检查是否有有效数据
const hasValidData = computed(() => {
  switch (state.chartType) {
    case 'feature':
      return state.featureData.length > 0;
    case 'as-outage':
      return state.asOutageData.length > 0;
    case 'prefix-outage':
      return state.prefixOutageData.length > 0;
    case 'resource':
      return state.resourceData.length > 0;
    default:
      return false;
  }
});

// 图表标题
const chartTitle = computed(() => {
  let typeText = '';
  switch (state.chartType) {
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
  
  if (detailType.value === 'country') {
    return `${targetName.value} 国家${typeText}`;
  } else {
    return `AS${targetName.value} ${typeText}`;
  }
});

// 初始化路由参数
const initRouteParams = () => {
  // 判断详情类型
  if (route.query.country) {
    detailType.value = 'country';
    targetName.value = route.query.country as string;
  } else if (route.query.asn) {
    detailType.value = 'as';
    targetName.value = route.query.asn as string;
  }
  
  state.timeRange[0] = route.query.start_time as string || '';
  state.timeRange[1] = route.query.end_time as string || '';
};

// 处理图表类型改变
const handleChartTypeChange = () => {
  // 清空当前数据
  state.featureData = [];
  state.asOutageData = [];
  state.prefixOutageData = [];
  state.resourceData = [];
  
  // 重新查询数据
  if (state.hasQueried) {
    handleQuery();
  }
};

// 获取Feature数据
const fetchFeatureData = async (target: string, startTime: string, endTime: string) => {
  let apiUrl = '';
  let params: any = {
    start_time: startTime,
    end_time: endTime,
    page_num: 1,
    page_size: 1
  };

  if (detailType.value === 'country') {
    apiUrl = `${baseUrl}features/countries`;
    params.country = target;
  } else {
    apiUrl = `${baseUrl}features/ases`;
    params.asn = target;
  }

  console.log('发送Feature请求到:', apiUrl, params);
  
  const response = await request.get(apiUrl, {
    params,
    timeout: 500000
  });
  
  console.log('Feature收到响应:', response);
  
  if (response && response.data && Array.isArray(response.data) && response.data.length > 0) {
    const firstItem = response.data[0];
    if (firstItem && firstItem.time_series_data && Array.isArray(firstItem.time_series_data)) {
      state.featureData = buildFeatureSeries(firstItem.time_series_data, startTime, endTime);
      
      if (state.featureData.length > 0) {
        // ElMessage.success(`成功获取${target}的Feature数据，共${state.featureData.length}条记录`);
      } else {
        ElMessage.warning(`${target}在指定时间范围内暂无Feature数据`);
      }
    } else {
      state.featureData = [];
      ElMessage.warning('Feature数据格式异常');
    }
  } else {
    state.featureData = [];
    ElMessage.warning(`未找到${target}的Feature数据`);
  }
};

// 获取AS中断数据
const fetchAsOutageData = async (country: string, startTime: string, endTime: string) => {
  const apiUrl = `${baseUrl}features/outages/country-as`;
  const params = {
    country: country,
    start_time: startTime,
    end_time: endTime
  };

  console.log('发送AS中断请求到:', apiUrl, params);
  
  const response = await request.get(apiUrl, {
    params,
    timeout: 500000
  });
  
  console.log('AS中断收到响应:', response);
  
  // 根据后端接口，数据可能直接是数组，也可能包装在response.data中
  let data = null;
  if (response && Array.isArray(response)) {
    data = response;
  } else if (response && response.data && Array.isArray(response.data)) {
    data = response.data;
  } else if (response && typeof response === 'object' && response.data) {
    // 检查是否是JSON字符串需要解析
    try {
      data = JSON.parse(response.data);
    } catch {
      data = response.data;
    }
  }
  
  if (data && Array.isArray(data)) {
    state.asOutageData = data;
    
    if (state.asOutageData.length > 0) {
      // ElMessage.success(`成功获取${country}的AS中断数据，共${state.asOutageData.length}条记录`);
    } else {
      ElMessage.warning(`${country}在指定时间范围内暂无AS中断数据`);
    }
  } else {
    state.asOutageData = [];
    ElMessage.warning(`未找到${country}的AS中断数据`);
  }
};

// 获取Prefix中断数据
const fetchPrefixOutageData = async (target: string, startTime: string, endTime: string) => {
  let apiUrl = '';
  let params: any = {
    start_time: startTime,
    end_time: endTime
  };

  if (detailType.value === 'country') {
    apiUrl = `${baseUrl}features/outages/country-prefix`;
    params.country = target;
  } else {
    apiUrl = `${baseUrl}features/outages/as-prefix`;
    params.asn = target;
  }

  console.log('发送Prefix中断请求到:', apiUrl, params);
  
  const response = await request.get(apiUrl, {
    params,
    timeout: 500000
  });
  
  console.log('Prefix中断收到响应:', response);
  
  // 根据后端接口，数据可能直接是数组，也可能包装在response.data中
  let data = null;
  if (response && Array.isArray(response)) {
    data = response;
  } else if (response && response.data && Array.isArray(response.data)) {
    data = response.data;
  } else if (response && typeof response === 'object' && response.data) {
    // 检查是否是JSON字符串需要解析
    try {
      data = JSON.parse(response.data);
    } catch {
      data = response.data;
    }
  }
  
  if (data && Array.isArray(data)) {
    state.prefixOutageData = data;
    
    if (state.prefixOutageData.length > 0) {
      // ElMessage.success(`成功获取${target}的Prefix中断数据，共${state.prefixOutageData.length}条记录`);
    } else {
      ElMessage.warning(`${target}在指定时间范围内暂无Prefix中断数据`);
    }
  } else {
    state.prefixOutageData = [];
    ElMessage.warning(`未找到${target}的Prefix中断数据`);
  }
};

// 获取Resource数据
const fetchResourceData = async (target: string, startTime: string, endTime: string) => {
  let apiUrl = '';
  let params: any = {
    start_time: startTime,
    end_time: endTime,
    page_num: 1,
    page_size: 1
  };

  if (detailType.value === 'country') {
    apiUrl = `${baseUrl}features/countries`;
    params.country = target;
  } else {
    apiUrl = `${baseUrl}features/ases`;
    params.asn = target;
  }

  console.log('发送Resource请求到:', apiUrl, params);
  
  const response = await request.get(apiUrl, {
    params,
    timeout: 500000
  });
  
  console.log('Resource收到响应:', response);
  
  if (response && response.data && Array.isArray(response.data) && response.data.length > 0) {
    const firstItem = response.data[0];
    if (firstItem && firstItem.time_series_data && Array.isArray(firstItem.time_series_data)) {
      // 过滤只包含资源相关字段的数据
      state.resourceData = buildResourceSeries(firstItem.time_series_data, startTime, endTime);
      
      if (state.resourceData.length > 0) {
        // ElMessage.success(`成功获取${target}的Resource数据，共${state.resourceData.length}条记录`);
      } else {
        ElMessage.warning(`${target}在指定时间范围内暂无IP资源数据`);
      }
    } else {
      state.resourceData = [];
      ElMessage.warning('Resource数据格式异常');
    }
  } else {
    state.resourceData = [];
    ElMessage.warning(`未找到${target}的IP资源数据`);
  }
};

// 获取数据
const fetchData = async (target: string, startTime: string, endTime: string) => {
  state.loading = true;
  state.error = null;

  try {
    switch (state.chartType) {
      case 'feature':
        await fetchFeatureData(target, startTime, endTime);
        break;
      case 'as-outage':
        if (detailType.value === 'country') {
          await fetchAsOutageData(target, startTime, endTime);
        }
        break;
      case 'prefix-outage':
        await fetchPrefixOutageData(target, startTime, endTime);
        break;
      case 'resource':
        await fetchResourceData(target, startTime, endTime);
        break;
    }
  } catch (error: any) {
    console.error('请求失败:', error);
    state.error = error as string;
    
    // 根据错误类型给出不同提示
    if (error.code === 'ECONNABORTED' || error.message?.includes('timeout')) {
      ElMessage.error('请求超时，请稍后重试');
    } else if (error.response?.status === 404) {
      ElMessage.error('API接口不存在，请联系管理员');
    } else if (error.response?.status >= 500) {
      ElMessage.error('服务器内部错误，请稍后重试');
    } else {
      ElMessage.error('网络请求失败，请检查网络连接');
    }
    
    // 清空当前图表类型的数据
    switch (state.chartType) {
      case 'feature':
        state.featureData = [];
        break;
      case 'as-outage':
        state.asOutageData = [];
        break;
      case 'prefix-outage':
        state.prefixOutageData = [];
        break;
      case 'resource':
        state.resourceData = [];
        break;
    }
  } finally {
    state.loading = false;
  }
};

// 处理查询
const handleQuery = async () => {
  const timeRange = getNormalizedTimeRange();
  if (!timeRange) {
    ElMessage.error('请选择时间范围！');
    return;
  }

  state.hasQueried = true;
  state.timeRange = timeRange;
  await fetchData(targetName.value, timeRange[0], timeRange[1]);
};

// 导出图片
const exportChart = () => {
  let chartRef = null;
  let fileName = '';

  switch (state.chartType) {
    case 'feature':
      chartRef = featureChartRef.value;
      fileName = `${targetName.value}_Feature时序图.png`;
      break;
    case 'as-outage':
      chartRef = asOutageChartRef.value;
      fileName = `${targetName.value}_AS中断时序图.png`;
      break;
    case 'prefix-outage':
      chartRef = prefixOutageChartRef.value;
      fileName = `${targetName.value}_Prefix中断时序图.png`;
      break;
    case 'resource':
      chartRef = resourceChartRef.value;
      fileName = `${targetName.value}_IP资源时序图.png`;
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
};

// 格式化日期时间
const formatDateTime = (date: Date): string => {
  const Y = date.getFullYear();
  const M = (date.getMonth() + 1).toString().padStart(2, '0');
  const D = date.getDate().toString().padStart(2, '0');
  const h = date.getHours().toString().padStart(2, '0');
  const m = date.getMinutes().toString().padStart(2, '0');
  const s = date.getSeconds().toString().padStart(2, '0');
  return `${Y}-${M}-${D} ${h}:${m}:${s}`;
};

const normalizeDateTimeValue = (value: string | Date | undefined): string => {
  if (!value) return '';
  if (value instanceof Date) {
    return formatDateTime(value);
  }
  const parsed = parseDateTime(value);
  return parsed ? formatDateTime(parsed) : '';
};

const getNormalizedTimeRange = (): [string, string] | null => {
  const startTime = normalizeDateTimeValue(state.timeRange[0]);
  const endTime = normalizeDateTimeValue(state.timeRange[1]);

  if (!startTime || !endTime) {
    return null;
  }

  return [startTime, endTime];
};

const getAuthHeaders = () => {
  const tokenItem = localStorage.getItem('token');
  if (!tokenItem) {
    return {};
  }

  try {
    const token = JSON.parse(tokenItem);
    if (token?.token) {
      return {
        Authorization: `Bearer ${token.token}`,
      };
    }
  } catch (error) {
    console.error('解析 token 失败:', error);
  }

  return {};
};

const getDownloadFileName = (contentDisposition: string | undefined, fallbackName: string) => {
  if (!contentDisposition) {
    return fallbackName;
  }

  const utf8Match = contentDisposition.match(/filename\*=UTF-8''([^;]+)/i);
  if (utf8Match?.[1]) {
    return decodeURIComponent(utf8Match[1]);
  }

  const normalMatch = contentDisposition.match(/filename[^;=\n]*=((['"]).*?\2|[^;\n]*)/i);
  if (normalMatch?.[1]) {
    return decodeURIComponent(normalMatch[1].replace(/['"]/g, ''));
  }

  return fallbackName;
};

const exportCountryOutageData = async (type: 'as' | 'prefix') => {
  if (detailType.value !== 'country') {
    return;
  }

  const timeRange = getNormalizedTimeRange();
  if (!timeRange) {
    ElMessage.error('请选择时间范围！');
    return;
  }

  const [startTime, endTime] = timeRange;
  state.timeRange = [startTime, endTime];

  const isAsExport = type === 'as';
  const url = `${baseUrl}features/outages/export/${isAsExport ? 'country-as' : 'country-prefix'}`;
  const fallbackFileName = `${targetName.value}_${isAsExport ? 'AS中断明细' : 'Prefix中断明细'}_${startTime.replace(/[: ]/g, '-')}_${endTime.replace(/[: ]/g, '-')}.csv`;

  if (isAsExport) {
    state.exportingAs = true;
  } else {
    state.exportingPrefix = true;
  }

  try {
    const response = await axios.get(url, {
      params: {
        country: targetName.value,
        start_time: startTime,
        end_time: endTime,
      },
      headers: getAuthHeaders(),
      responseType: 'blob',
      timeout: 600000,
    });

    const blob = new Blob([response.data], { type: 'text/csv;charset=utf-8;' });
    const downloadUrl = window.URL.createObjectURL(blob);
    const link = document.createElement('a');
    link.href = downloadUrl;
    link.download = getDownloadFileName(response.headers['content-disposition'], fallbackFileName);
    document.body.appendChild(link);
    link.click();
    document.body.removeChild(link);
    window.URL.revokeObjectURL(downloadUrl);

    ElMessage.success(`${isAsExport ? 'AS中断明细' : 'Prefix中断明细'}导出成功`);
  } catch (error) {
    console.error('导出中断明细失败:', error);
    ElMessage.error('导出失败，请稍后重试');
  } finally {
    if (isAsExport) {
      state.exportingAs = false;
    } else {
      state.exportingPrefix = false;
    }
  }
};

onMounted(() => {
  console.log('详情页组件挂载开始');
  
  // 初始化路由参数
  initRouteParams();
  
  // 延迟执行初始查询
  setTimeout(() => {
    handleQuery();
  }, 100);
});

onUnmounted(() => {
  console.log('详情页组件销毁');
});
</script>

<style scoped>
.feature-detail-container {
  height: 100vh;
  display: flex;
  flex-direction: column;
  padding: 20px;
  box-sizing: border-box;
  background-color: #f5f7fa;
}

.detail-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 20px;
  padding: 20px;
  background: white;
  border-radius: 8px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
}

.title-section .detail-title {
  margin: 0 0 8px 0;
  font-size: 24px;
  font-weight: bold;
  color: #303133;
}

.title-section .subtitle {
  font-size: 14px;
  color: #909399;
}

.control-section {
  display: flex;
  align-items: center;
  gap: 12px;
}

.chart-section {
  flex: 1;
  position: relative;
  background: white;
  border-radius: 8px;
  overflow: hidden;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.1);
  min-height: 500px;
}

/* 加载状态样式 */
.loading-overlay {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  background: rgba(255, 255, 255, 0.9);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  backdrop-filter: blur(2px);
}

.loading-content {
  text-align: center;
  padding: 40px;
  border-radius: 12px;
  background: white;
  box-shadow: 0 4px 20px rgba(0, 0, 0, 0.1);
  border: 1px solid #e1e8ed;
}

.loading-icon {
  font-size: 40px;
  color: #409eff;
  margin-bottom: 16px;
}

.loading-text {
  font-size: 16px;
  color: #303133;
  font-weight: 500;
  margin-bottom: 8px;
}

.loading-subtitle {
  font-size: 14px;
  color: #909399;
}

/* 空数据状态样式 */
.empty-state {
  position: absolute;
  top: 0;
  left: 0;
  right: 0;
  bottom: 0;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  background: white;
  z-index: 500;
}

.empty-tips {
  margin-top: 16px;
  font-size: 14px;
  color: #909399;
  text-align: left;
  max-width: 400px;
  line-height: 1.6;
}

.empty-tips > div {
  margin-bottom: 4px;
}

.empty-tips > div:first-child {
  font-weight: 500;
  color: #606266;
  margin-bottom: 8px;
}

/* 辅助样式 */
.mr10 {
  margin-right: 10px;
}
</style> 
