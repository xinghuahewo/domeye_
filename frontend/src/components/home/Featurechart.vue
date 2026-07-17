<template>
  <div class="home-feature-panel">
    <div class="panel-toolbar">
      <div class="panel-title">{{ chartTitle }}</div>
      <div class="panel-controls">
        <el-select
          v-model="state.countrySelect"
          class="country-select"
          size="default"
          filterable
          placeholder="选择国家或全球"
          :disabled="state.loading"
          @change="handleCountrySelectChange"
        >
          <el-option label="全球" :value="GLOBAL_COUNTRY_VALUE" />
          <el-option
            v-for="option in sortedCountryOptions"
            :key="option.value"
            :label="option.label"
            :value="option.value"
          />
        </el-select>

        <el-select
          v-model="state.chartType"
          class="type-select"
          size="default"
          :disabled="state.loading"
          @change="handleChartTypeChange"
        >
          <el-option label="报文时序图" value="feature" />
          <el-option label="AS中断时序图" value="as-outage" />
          <el-option label="Prefix中断时序图" value="prefix-outage" />
          <el-option label="IP资源时序图" value="resource" />
        </el-select>

        <el-date-picker
          v-model="state.timeRange"
          type="datetimerange"
          range-separator="至"
          start-placeholder="开始时间"
          end-placeholder="结束时间"
          class="date-range-picker"
          size="default"
          :disabled="state.loading"
        />

        <el-button
          type="primary"
          size="default"
          :loading="state.loading"
          :disabled="state.loading"
          @click="handleQuery"
        >
          查询
        </el-button>
      </div>
    </div>

    <div class="panel-body">
      <div class="panel-body-chart">
        <FeatureChart
          v-if="state.chartType === 'feature'"
        :data="state.featureData"
        :title="chartTitle"
        :loading="state.loading"
        ref="featureChartRef"
      />

      <AsOutageChart
        v-else-if="state.chartType === 'as-outage'"
        :data="state.asOutageData"
        :title="chartTitle"
        :loading="state.loading"
        ref="asOutageChartRef"
      />

      <PrefixOutageChart
        v-else-if="state.chartType === 'prefix-outage'"
        :data="state.prefixOutageData"
        :title="chartTitle"
        :loading="state.loading"
        ref="prefixOutageChartRef"
      />

      <ResourceChart
        v-else
        :data="state.resourceData"
        :title="chartTitle"
        :loading="state.loading"
        ref="resourceChartRef"
      />
      </div>

      <div v-if="state.loading" class="loading-overlay">
        <div class="loading-content">
          <el-icon class="is-loading loading-icon">
            <Loading />
          </el-icon>
          <div class="loading-text">正在加载{{ chartTitle }}</div>
          <div class="loading-subtitle">请稍候</div>
        </div>
      </div>

      <div v-if="!state.loading && !hasValidData && state.hasQueried" class="empty-state">
        <el-empty description="暂无数据" />
        <div class="empty-tips">
          请尝试调整时间范围，或通过下拉框选择正确的国家后重新查询
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue';
import { Loading } from '@element-plus/icons-vue';
import { ElMessage } from 'element-plus';
import request from '/@/utils/request';
import baseUrl from '/@/api';
import FeatureChart from '/@/components/feature/FeatureChart.vue';
import AsOutageChart from '/@/components/feature/AsOutageChart.vue';
import PrefixOutageChart from '/@/components/feature/PrefixOutageChart.vue';
import ResourceChart from '/@/components/feature/ResourceChart.vue';
import { COUNTRY_OPTIONS, type CountryOption } from '/@/utils/countryOptions';

type ScopeType = 'global' | 'country';
type ChartType = 'feature' | 'as-outage' | 'prefix-outage' | 'resource';

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

type OutagePoint = {
  time_slot: string;
  outage_count: number;
};

const FEATURE_INTERVAL_MS = 5 * 60 * 1000;

const featureChartRef = ref<InstanceType<typeof FeatureChart> | null>(null);
const asOutageChartRef = ref<InstanceType<typeof AsOutageChart> | null>(null);
const prefixOutageChartRef = ref<InstanceType<typeof PrefixOutageChart> | null>(null);
const resourceChartRef = ref<InstanceType<typeof ResourceChart> | null>(null);
const countryOptions: CountryOption[] = COUNTRY_OPTIONS;
const GLOBAL_COUNTRY_VALUE = 'global';
const DEFAULT_COUNTRY_VALUE = '伊朗';
const DEFAULT_RANGE_DAYS = 7;

const state = reactive({
  chartType: 'resource' as ChartType,
  countrySelect: DEFAULT_COUNTRY_VALUE,
  appliedScope: 'country' as ScopeType,
  appliedQuery: DEFAULT_COUNTRY_VALUE,
  timeRange: ['', ''] as Array<string | Date>,
  featureData: [] as FeaturePoint[],
  asOutageData: [] as OutagePoint[],
  prefixOutageData: [] as OutagePoint[],
  resourceData: [] as ResourcePoint[],
  loading: false,
  hasQueried: false,
  error: null as string | null,
});

const scopeLabel = computed(() => (
  state.appliedScope === 'global' ? '全球' : (state.appliedQuery.trim() || '国家')
));
const sortedCountryOptions = computed(() => (
  [...countryOptions].sort((a, b) => a.label.localeCompare(b.label, 'zh-CN-u-co-pinyin'))
));
const currentScope = computed<ScopeType>(() => (
  state.countrySelect === GLOBAL_COUNTRY_VALUE ? 'global' : 'country'
));

const chartTitle = computed(() => {
  const prefix = scopeLabel.value;
  switch (state.chartType) {
    case 'feature':
      return `${prefix}报文时序图`;
    case 'as-outage':
      return `${prefix}AS中断时序图`;
    case 'prefix-outage':
      return `${prefix}Prefix中断时序图`;
    case 'resource':
      return `${prefix}IP资源时序图`;
  }
});

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
  }
});

const parseDateTime = (value?: string): Date | null => {
  if (!value) return null;
  const normalized = value.includes('T') ? value : value.replace(' ', 'T');
  const parsed = new Date(normalized);
  return Number.isNaN(parsed.getTime()) ? null : parsed;
};

const formatDateTime = (date: Date): string => {
  const Y = date.getFullYear();
  const M = String(date.getMonth() + 1).padStart(2, '0');
  const D = String(date.getDate()).padStart(2, '0');
  const h = String(date.getHours()).padStart(2, '0');
  const m = String(date.getMinutes()).padStart(2, '0');
  const s = String(date.getSeconds()).padStart(2, '0');
  return `${Y}-${M}-${D} ${h}:${m}:${s}`;
};

const normalizeDateTimeValue = (value: string | Date | undefined): string => {
  if (!value) return '';
  if (value instanceof Date) return formatDateTime(value);
  const parsed = parseDateTime(value);
  return parsed ? formatDateTime(parsed) : '';
};

const getNormalizedTimeRange = (): [string, string] | null => {
  const startTime = normalizeDateTimeValue(state.timeRange[0]);
  const endTime = normalizeDateTimeValue(state.timeRange[1]);
  if (!startTime || !endTime) return null;
  return [startTime, endTime];
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

  const firstKnownSlot = normalized[0].timestamp;
  const lastKnownSlot = normalized[normalized.length - 1].timestamp;
  const boundedStartSlot = Math.max(startSlot, firstKnownSlot);
  const boundedEndSlot = Math.min(endSlot, lastKnownSlot);
  if (boundedStartSlot > boundedEndSlot) {
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
  for (let slot = boundedStartSlot; slot <= boundedEndSlot; slot += FEATURE_INTERVAL_MS) {
    filled.push(pointMap.get(slot) || {
      time: formatDateTime(new Date(slot)),
      announce: 0,
      withdraw: 0,
    });
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

const resetChartData = () => {
  state.featureData = [];
  state.asOutageData = [];
  state.prefixOutageData = [];
  state.resourceData = [];
};

const getSelectedCountryOption = () => (
  countryOptions.find((item) => item.value === state.countrySelect) || null
);

const getResolvedCountryLabel = () => {
  if (currentScope.value === 'global') return '';
  return getSelectedCountryOption()?.label || state.countrySelect;
};

const getResolvedCountryQuery = () => {
  if (currentScope.value === 'global') return '';
  const selected = getSelectedCountryOption();
  return selected?.queryValue || selected?.value || state.countrySelect;
};

const getFeatureTarget = () => (
  currentScope.value === 'global' ? 'collector' : getResolvedCountryQuery()
);

const handleCountrySelectChange = async (value: string | undefined) => {
  if (!value) {
    state.countrySelect = GLOBAL_COUNTRY_VALUE;
    return;
  }

  if (state.hasQueried) {
    await handleQuery();
  }
};

const handleChartTypeChange = async () => {
  if (state.hasQueried) {
    await handleQuery();
  }
};

const fetchFeatureOrResourceData = async (startTime: string, endTime: string) => {
  const response = await request.get(`${baseUrl}features/top`, {
    params: {
      target: getFeatureTarget(),
      start_time: startTime,
      end_time: endTime,
    },
    timeout: 500000,
  });

  const rawData = Array.isArray(response) ? response as RawFeaturePoint[] : [];
  if (state.chartType === 'feature') {
    state.featureData = buildFeatureSeries(rawData, startTime, endTime);
  } else {
    state.resourceData = buildResourceSeries(rawData, startTime, endTime);
  }
};

const normalizeOutageResponse = (response: unknown): OutagePoint[] => {
  if (Array.isArray(response)) return response as OutagePoint[];
  if (response && typeof response === 'object' && Array.isArray((response as { data?: OutagePoint[] }).data)) {
    return (response as { data: OutagePoint[] }).data;
  }
  return [];
};

const fetchAsOutageData = async (startTime: string, endTime: string) => {
  const url = currentScope.value === 'global'
    ? `${baseUrl}features/outages/global-as`
    : `${baseUrl}features/outages/country-as`;
  const params: Record<string, string> = { start_time: startTime, end_time: endTime };
  const countryQuery = getResolvedCountryQuery();
  if (currentScope.value === 'country' && countryQuery) {
    params.country = countryQuery;
  }
  const response = await request.get(url, { params, timeout: 500000 });
  state.asOutageData = normalizeOutageResponse(response);
};

const fetchPrefixOutageData = async (startTime: string, endTime: string) => {
  const url = currentScope.value === 'global'
    ? `${baseUrl}features/outages/global-prefix`
    : `${baseUrl}features/outages/country-prefix`;
  const params: Record<string, string> = { start_time: startTime, end_time: endTime };
  const countryQuery = getResolvedCountryQuery();
  if (currentScope.value === 'country' && countryQuery) {
    params.country = countryQuery;
  }
  const response = await request.get(url, { params, timeout: 500000 });
  state.prefixOutageData = normalizeOutageResponse(response);
};

const fetchData = async (startTime: string, endTime: string) => {
  state.loading = true;
  state.error = null;
  resetChartData();

  try {
    if (state.chartType === 'feature' || state.chartType === 'resource') {
      await fetchFeatureOrResourceData(startTime, endTime);
    } else if (state.chartType === 'as-outage') {
      await fetchAsOutageData(startTime, endTime);
    } else {
      await fetchPrefixOutageData(startTime, endTime);
    }
  } catch (error: any) {
    console.error('首页图表请求失败:', error);
    state.error = typeof error?.message === 'string' ? error.message : 'request_failed';
    resetChartData();
    if (error?.code === 'ECONNABORTED' || error?.message?.includes('timeout')) {
      ElMessage.error('请求超时，请稍后重试');
    } else if (error?.response?.status === 404) {
      ElMessage.error('接口不存在，请检查后端路由');
    } else if (error?.response?.status >= 500) {
      ElMessage.error('服务器内部错误，请稍后重试');
    } else {
      ElMessage.error('图表数据请求失败');
    }
  } finally {
    state.loading = false;
  }
};

const handleQuery = async () => {
  const timeRange = getNormalizedTimeRange();
  if (!timeRange) {
    ElMessage.error('请选择时间范围');
    return;
  }

  const countryQuery = getResolvedCountryQuery();
  if (currentScope.value === 'country' && !countryQuery) {
    ElMessage.error('请选择国家');
    return;
  }

  state.hasQueried = true;
  state.appliedScope = currentScope.value;
  state.appliedQuery = getResolvedCountryLabel();
  state.timeRange = timeRange;
  await fetchData(timeRange[0], timeRange[1]);
};

onMounted(() => {
  const now = new Date();
  const oneWeekAgo = new Date(now.getTime() - DEFAULT_RANGE_DAYS * 24 * 60 * 60 * 1000);
  state.timeRange = [formatDateTime(oneWeekAgo), formatDateTime(now)];
  handleQuery();
});
</script>

<style scoped lang="scss">
.home-feature-panel {
  display: flex;
  flex-direction: column;
  flex: 1;
  min-height: 0;
}

.panel-toolbar {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 16px;
  margin-bottom: 16px;
  flex-wrap: wrap;
}

.panel-title {
  font-size: 16px;
  font-weight: 600;
  line-height: 32px;
  color: #303133;
}

.panel-controls {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}

.type-select,
.country-select {
  width: 148px;
}

.date-range-picker {
  width: 380px;
}

.panel-body {
  position: relative;
  flex: 1;
  min-height: 500px;
  display: flex;
  flex-direction: column;
}

.panel-body-chart {
  flex: 1;
  min-height: 0;
  position: relative;
}

.loading-overlay {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  background: rgba(255, 255, 255, 0.88);
  z-index: 10;
  backdrop-filter: blur(2px);
}

.loading-content {
  text-align: center;
  padding: 36px 42px;
  border-radius: 12px;
  background: #fff;
  box-shadow: 0 8px 24px rgba(31, 35, 41, 0.08);
}

.loading-icon {
  font-size: 36px;
  color: #409eff;
  margin-bottom: 14px;
}

.loading-text {
  font-size: 16px;
  color: #303133;
  margin-bottom: 6px;
}

.loading-subtitle,
.empty-tips {
  font-size: 13px;
  color: #909399;
}

.empty-state {
  min-height: 500px;
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
}

@media (max-width: 1200px) {
  .panel-controls {
    width: 100%;
  }

  .date-range-picker,
  .country-select,
  .type-select {
    width: 100%;
  }
}
</style>
