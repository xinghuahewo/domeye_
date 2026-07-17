<template>
  <div class="home-container">
    <el-row :gutter="15" class="home-card-hero">
      <el-col :xs="24" :sm="24" :md="24" :lg="24" :xl="24">
        <div class="home-card-item hero-card" v-loading="state.collectorLoading">
          <!-- 路由状态与更新时间已隐藏
          <div class="hero-card__meta">
            <span class="hero-card__status" :class="`hero-card__status--${statusTagType}`">
              <span class="hero-card__status-dot"></span>
              {{ statusText }}
            </span>
            <span class="hero-card__meta-divider">·</span>
            <span class="hero-card__meta-item">{{ valueOrDash(currentCollectorData.time) }}</span>
          </div>
          -->

          <div class="metric-grid">
            <div
              v-for="metric in metricCards"
              :key="metric.key"
              class="metric-card"
              :class="`metric-card--${metric.key}`"
            >
              <span class="metric-card__label">{{ metric.label }}</span>
              <span
                class="metric-card__value"
                :class="`metric-card__value--${metric.key}`"
                :title="metric.rawValue ?? metric.value"
              >
                {{ metric.value }}
              </span>
              <span class="metric-card__hint">{{ metric.hint }}</span>
            </div>
          </div>
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="15" class="home-card-two">
      <el-col :xs="24" :sm="24" :md="24" :lg="24" :xl="24" class="home-media">
        <div class="home-card-item home-card-item--feature">
          <Featurechart />
        </div>
      </el-col>
    </el-row>

    <el-row :gutter="15" class="home-card-two">
      <el-col :xs="24" :sm="24" :md="24" :lg="24" :xl="24" class="home-media">
        <div class="home-card-item home-card-item--table">
          <div class="home-card-item-title">高危路由事件</div>
          <el-button
            type="primary"
            size="small"
            text
            class="home-card-item__more"
            @click="toMore"
          >
            更多
          </el-button>
          <el-table
            :data="tableData"
            :row-key="getRowKey"
            v-loading="state.eventLoading"
            style="width: 100%;"
            size="default"
            border
          >
            <el-table-column type="index" label="序号" width="60">
              <template #default="scope">
                {{ scope.$index + 1 }}
              </template>
            </el-table-column>
            <el-table-column prop="event_type" label="事件类型" width="110" />
            <el-table-column prop="affected_prefix" label="影响前缀" min-width="130" />
            <el-table-column prop="attacked_as" label="受害方AS" min-width="100" />
            <el-table-column prop="attacked_org" label="受害方机构" min-width="100" />
            <el-table-column prop="attacked_country" label="受害方国家" width="120" />
            <el-table-column prop="attacker_as" label="肇事方AS" min-width="100" />
            <el-table-column prop="attacker_org" label="肇事方机构" min-width="100" />
            <el-table-column prop="attacker_country" label="肇事方国家" width="120" />
            <el-table-column prop="level" label="事件等级" width="110">
              <template #default="scope">
                <span :class="{ highLevel: scope.row.level === 'high', middleLevel: scope.row.level === 'middle', lowLevel: scope.row.level === 'low' }">
                  高危事件
                </span>
              </template>
            </el-table-column>
            <el-table-column prop="start_time" width="110" label="开始时间" />
            <el-table-column prop="end_time" width="110" label="结束时间" />
            <el-table-column label="操作" width="120">
              <template #default="scope">
                <div class="home-card-item__actions">
                  <el-button type="primary" link @click="toDetail(scope.row.detail_url, scope.row.event_type)">详情</el-button>
                  <TemplateSelectionDialog :detail-url="scope.row.detail_url" />
                </div>
              </template>
            </el-table-column>
          </el-table>
        </div>
      </el-col>
    </el-row>
  </div>
</template>

<script lang="ts" setup>
import { computed, onBeforeMount, reactive, ref, watch } from 'vue';
import { storeToRefs } from 'pinia';
import { useRouter } from 'vue-router';
import Featurechart from '/@/components/home/Featurechart.vue';
import TemplateSelectionDialog from '/@/components/home/TemplateSelectionDialog.vue';
import request from '/@/utils/request';
import { useCollectorStore } from '/@/stores/collector';
import type { HomeEventRow, VantagePointState } from '/@/types/collector';

interface HomeMetricCard {
  key: string;
  label: string;
  value: string;
  rawValue?: string;
  hint: string;
}

const topEventTypes = "('前缀劫持','子前缀劫持', '前缀中断', 'AS中断','国家中断', '路由泄漏')";

const router = useRouter();
const collectorStore = useCollectorStore();
const { activeCollectorId, activeCollector } = storeToRefs(collectorStore);

const tableData = ref<HomeEventRow[]>([]);
const collectorCache = reactive<Record<string, VantagePointState>>({});
const state = reactive({
  collectorLoading: false,
  eventLoading: false,
});

const currentCollectorData = computed<VantagePointState>(() => collectorCache[activeCollectorId.value] || {});
const currentCollectorLabel = computed(() => activeCollector.value?.alias || activeCollector.value?.label || activeCollectorId.value);

const hasCollectorState = (value?: VantagePointState) => {
  return !!value && typeof value === 'object' && !Array.isArray(value) && !!value.time;
};

const normalizeCollectorState = (value: unknown): VantagePointState => {
  if (Array.isArray(value)) return (value[0] as VantagePointState) || {};
  if (value && typeof value === 'object') return value as VantagePointState;
  return {};
};

const getCollectorStatus = (isOutlier?: boolean) => {
  if (isOutlier === true) return { text: '异常', type: 'danger' as const };
  if (isOutlier === false) return { text: '正常', type: 'success' as const };
  return { text: '未知', type: 'info' as const };
};

const statusInfo = computed(() => getCollectorStatus(currentCollectorData.value.is_outlier));
const statusText = computed(() => statusInfo.value.text);
const statusTagType = computed(() => statusInfo.value.type);

const valueOrDash = (value?: string | number | boolean) => {
  if (value === undefined || value === null || value === '') return '-';
  return String(value);
};

const formatCompact = (n: string | number | boolean): string => {
  const num = typeof n === 'number' ? n : parseFloat(String(n));
  if (Number.isNaN(num) || !Number.isFinite(num)) return valueOrDash(n);
  if (num >= 1e12) return (num / 1e12).toFixed(1) + 'T';
  if (num >= 1e9) return (num / 1e9).toFixed(1) + 'B';
  if (num >= 1e6) return (num / 1e6).toFixed(1) + 'M';
  if (num >= 1e3) return (num / 1e3).toFixed(1) + 'K';
  return String(Math.round(num));
};

const composeMetricValue = (primaryLabel: string, primaryValue?: string | number | boolean, secondaryLabel?: string, secondaryValue?: string | number | boolean, compact = false) => {
  const p = compact ? formatCompact(primaryValue ?? '') : valueOrDash(primaryValue);
  if (!secondaryLabel) return `${primaryLabel} ${p}`;
  const s = compact ? formatCompact(secondaryValue ?? '') : valueOrDash(secondaryValue);
  return `${primaryLabel} ${p} / ${secondaryLabel} ${s}`;
};

const formatRange = (lower?: string | number, upper?: string | number) => {
  if (lower === undefined || lower === null || lower === '' || upper === undefined || upper === null || upper === '') {
    return '正常区间待接入';
  }
  return `${lower}~${upper}`;
};

const formatRangeSmart = (lower?: string | number, upper?: string | number) => {
  if (lower === undefined || lower === null || lower === '' || upper === undefined || upper === null || upper === '') {
    return '正常区间待接入';
  }
  const l = typeof lower === 'number' ? lower : parseFloat(String(lower));
  const u = typeof upper === 'number' ? upper : parseFloat(String(upper));
  const shouldCompact = (Number.isFinite(l) && Math.abs(l) >= 1e9) || (Number.isFinite(u) && Math.abs(u) >= 1e9);
  if (!shouldCompact) return `${lower}~${upper}`;
  return `${formatCompact(lower)}~${formatCompact(upper)}`;
};

const metricCards = computed<HomeMetricCard[]>(() => {
  const data = currentCollectorData.value;
  const ipv4Raw = composeMetricValue('数量', data.ipv4_address_count, '前缀', data.ipv4_prefix_count);
  const ipv6Raw = composeMetricValue('数量', data.ipv6_48_count, '前缀', data.ipv6_prefix_count);
  const pathsRaw = composeMetricValue('数量', data.path_count);
  return [
    {
      key: 'ipv4',
      label: 'IPv4',
      value: composeMetricValue('数量', data.ipv4_address_count, '前缀', data.ipv4_prefix_count, true),
      rawValue: ipv4Raw,
      hint: `前缀正常区间 ${formatRangeSmart(data.ipv4_prefix_normal_lower, data.ipv4_prefix_normal_upper)}`,
    },
    {
      key: 'ipv6',
      label: 'IPv6',
      value: composeMetricValue('数量', data.ipv6_48_count, '前缀', data.ipv6_prefix_count, true),
      rawValue: ipv6Raw,
      hint: `前缀正常区间 ${formatRangeSmart(data.ipv6_prefix_normal_lower, data.ipv6_prefix_normal_upper)}`,
    },
    {
      key: 'as',
      label: 'AS',
      value: composeMetricValue('公有', data.public_as_count, '私有', data.private_as_count),
      hint: `公有 ${formatRange(data.public_as_normal_lower, data.public_as_normal_upper)} / 私有 ${formatRange(data.private_as_normal_lower, data.private_as_normal_upper)}`,
    },
    {
      key: 'paths',
      label: '路径',
      value: composeMetricValue('数量', data.path_count, undefined, undefined, true),
      rawValue: pathsRaw,
      hint: `正常区间 ${formatRangeSmart(data.path_normal_lower, data.path_normal_upper)}`,
    },
    {
      key: 'vp',
      label: 'VP',
      value: composeMetricValue('数量', data.vp_count),
      hint: `当前采集点 ${currentCollectorLabel.value}`,
    },
  ];
});

const loadCollectorState = async (collectorId: string) => {
  if (hasCollectorState(collectorCache[collectorId])) return;
  const collector = collectorStore.collectorOptions.find((item) => item.id === collectorId);
  if (!collector) return;
  state.collectorLoading = true;
  try {
    const result = await request({
      url: 'dashboard/vantage-points/state',
      method: 'get',
      params: {
        collector: collector.collectorParam,
      },
    });
    collectorCache[collectorId] = normalizeCollectorState(result);
  } finally {
    state.collectorLoading = false;
  }
};

const loadTopEvents = async () => {
  state.eventLoading = true;
  try {
    const result = await request({
      url: 'events/top',
      method: 'get',
      params: {
        event_type: topEventTypes,
      },
    });
    tableData.value = Array.isArray(result) ? result : [];
  } finally {
    state.eventLoading = false;
  }
};

const toDetail = (detailUrl: string, eventType: string) => {
  const jumpUrl = router.resolve({ name: 'anomaly_detail', query: { detail_url: detailUrl, type: eventType } });
  window.open(jumpUrl.href);
};

const toMore = () => {
  const jumpUrl = router.resolve({ name: 'anomaly_judge' });
  window.open(jumpUrl.href);
};

const getRowKey = (row: HomeEventRow) => row.detail_url || `${row.event_type}-${row.start_time}-${row.end_time}`;

watch(
  activeCollectorId,
  async (collectorId) => {
    await loadCollectorState(collectorId);
  },
  { immediate: true }
);

onBeforeMount(async () => {
  await loadTopEvents();
});
</script>

<style scoped lang="scss">
.home-container {
  overflow: hidden;

  .home-card-item {
    width: 100%;
    border-radius: 4px;
    transition: all ease 0.3s;
    padding: 10px;
    overflow: hidden;
    background: var(--el-color-white);
    color: var(--el-text-color-primary);
    border: 1px solid var(--next-border-color-light);
    position: relative;

    &:hover {
      box-shadow: 0 2px 12px var(--next-color-dark-hover);
      transition: all ease 0.3s;
    }

    &-title {
      font-size: 12px;
      font-weight: bold;
      min-height: 18px;
    }

    &__more {
      position: absolute;
      right: 10px;
      top: 10px;
    }

    &__actions {
      display: flex;
      align-items: center;
      gap: 10px;
    }
  }

  .home-card-hero {
    .hero-card {
      min-height: 80px;
      padding: 16px 20px;
      border: 1px solid var(--el-border-color-light);
      background: var(--el-color-white);
      border-radius: 4px;
      box-shadow: none;

      &__meta {
        display: flex;
        align-items: center;
        flex-wrap: wrap;
        gap: 6px;
        margin-bottom: 14px;
        font-size: 12px;
        color: var(--el-text-color-secondary);
      }

      &__status {
        display: inline-flex;
        align-items: center;
        gap: 5px;
        font-weight: 500;
        color: var(--el-text-color-primary);

        &-dot {
          width: 5px;
          height: 5px;
          border-radius: 50%;
          flex-shrink: 0;
        }

        &--success .hero-card__status-dot { background: var(--el-color-success); }
        &--danger .hero-card__status-dot { background: var(--el-color-danger); }
        &--info .hero-card__status-dot { background: var(--el-color-info); }
      }

      &__meta-divider {
        color: var(--el-border-color);
        font-weight: 300;
      }

      &__meta-item {
        color: var(--el-text-color-secondary);
        font-weight: 400;
      }
    }

    .metric-grid {
      display: grid;
      grid-template-columns: repeat(5, minmax(0, 1fr));
      gap: 15px;
    }

    .metric-card {
      display: flex;
      flex-direction: column;
      gap: 4px;
      padding: 14px 16px;
      background: var(--el-fill-color-light);
      border-radius: 4px;
      min-height: 64px;
      min-width: 0;
      border: 1px solid transparent;
      transition: all 0.2s ease;

      &:hover {
        background: var(--el-color-primary-light-9);
        border-color: var(--el-color-primary-light-7);
      }

      &__label {
        display: flex;
        align-items: center;
        gap: 6px;
        font-size: 12px;
        color: var(--el-text-color-regular);
        font-weight: normal;

        &::before {
          content: '';
          display: block;
          width: 6px;
          height: 6px;
          border-radius: 50%;
          background-color: var(--dot-color, var(--el-text-color-placeholder));
        }
      }

      &__value {
        font-size: 16px;
        font-weight: bold;
        color: var(--el-text-color-primary);
        line-height: 1.3;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
        font-variant-numeric: tabular-nums;
      }

      &__hint {
        font-size: 11px;
        color: var(--el-text-color-secondary);
        line-height: 1.3;
        margin-top: 2px;
      }
    }

    .metric-card--ipv4 { --dot-color: var(--el-color-primary); }
    .metric-card--ipv6 { --dot-color: var(--el-color-success); }
    .metric-card--as { --dot-color: var(--el-color-warning); }
    .metric-card--paths { --dot-color: var(--el-color-info); }
    .metric-card--vp { --dot-color: var(--el-color-danger); }

    .metric-card__value--ipv4,
    .metric-card__value--ipv6 {
      white-space: normal;
      overflow: visible;
      text-overflow: clip;
      word-break: break-word;
    }
  }

  .home-card-two {
    margin-top: 4px;

    .home-card-item {
      height: 500px;
      width: 100%;
    }

    .home-card-item--feature {
      height: auto;
      min-height: 586px;
      padding-top: 4px;
      display: flex;
      flex-direction: column;
      overflow: visible;
    }

    .home-card-item--table {
      padding-top: 10px;
      overflow: auto;
    }

    .highLevel {
      color: red;
    }
  }
}

@media (max-width: 992px) {
  .home-container {
    .home-card-hero {
      .metric-grid {
        grid-template-columns: repeat(3, minmax(0, 1fr));
      }
    }
  }
}

@media (max-width: 768px) {
  .home-container {
    .home-card-hero {
      .metric-grid {
        grid-template-columns: repeat(2, minmax(0, 1fr));
      }
    }
  }
}

@media (max-width: 576px) {
  .home-container {
    .home-card-hero {
      .metric-grid {
        grid-template-columns: 1fr;
      }
    }
  }
}
</style>
