<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import {
  getASPrefixOutages,
  getCountryASOutages,
  getCountryPrefixOutages,
  getGlobalASOutages,
  getGlobalPrefixOutages,
  getTopFeatures,
  type FeatureRange,
} from '@/api/features'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import type { FeaturePoint, OutagePoint } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { recentRange, toBackendTime } from '@/utils/time'

const defaults = recentRange(24)
const query = reactive({
  target: 'collector',
  start: defaults.start,
  end: defaults.end,
})
const features = ref<FeaturePoint[]>([])
const outagePrimary = ref<OutagePoint[]>([])
const outageSecondary = ref<OutagePoint[]>([])
const loading = ref(false)
const featureError = ref('')
const outageError = ref('')

const targetMode = computed<'global' | 'as' | 'country'>(() => {
  const target = query.target.trim()
  if (/^(AS)?\d+$/i.test(target)) return 'as'
  if (/^(collector|路由采集点|rrc\d+)$/i.test(target)) return 'global'
  return 'country'
})

const targetLabel = computed(() => {
  if (targetMode.value === 'global') return '全球采集点'
  if (targetMode.value === 'as') return `AS${query.target.replace(/\D/g, '')}`
  return query.target.trim() || '国家未指定'
})

const messageSeries = computed<ChartSeries[]>(() => [
  {
    name: 'ANNOUNCE',
    color: '#0b57b7',
    data: features.value.map((point) => [point.time, point.announce]),
  },
  {
    name: 'WITHDRAW',
    color: '#35b6d4',
    data: features.value.map((point) => [point.time, point.withdraw]),
  },
])

const resourceSeries = computed<ChartSeries[]>(() => [
  {
    name: 'IPv4 PREFIX',
    color: '#175cd3',
    data: features.value
      .filter((point) => point.ipv4Prefixes !== null)
      .map((point) => [point.time, point.ipv4Prefixes]),
  },
  {
    name: 'IPv6 PREFIX',
    color: '#35b6d4',
    data: features.value
      .filter((point) => point.ipv6Prefixes !== null)
      .map((point) => [point.time, point.ipv6Prefixes]),
  },
])

const outageSeries = computed<ChartSeries[]>(() => {
  const primaryName = targetMode.value === 'as' ? 'PREFIX OUTAGE' : 'AS OUTAGE'
  const series: ChartSeries[] = [{
    name: primaryName,
    color: '#f48120',
    data: outagePrimary.value.map((point) => [point.time, point.count]),
  }]
  if (targetMode.value !== 'as') {
    series.push({
      name: 'PREFIX OUTAGE',
      color: '#c9372c',
      data: outageSecondary.value.map((point) => [point.time, point.count]),
    })
  }
  return series
})

function rangeParams(): FeatureRange {
  return {
    start_time: toBackendTime(query.start),
    end_time: toBackendTime(query.end),
  }
}

async function load() {
  featureError.value = ''
  outageError.value = ''
  features.value = []
  outagePrimary.value = []
  outageSecondary.value = []
  if (!query.target.trim() || !query.start || !query.end) {
    featureError.value = '目标和时间范围不能为空'
    return
  }
  if (new Date(query.start).getTime() >= new Date(query.end).getTime()) {
    featureError.value = '开始时间必须早于结束时间'
    return
  }

  loading.value = true
  const range = rangeParams()
  const featureTask = getTopFeatures(query.target.trim(), range)
  let outageTasks: [Promise<OutagePoint[]>, Promise<OutagePoint[]> | null]

  if (targetMode.value === 'as') {
    outageTasks = [getASPrefixOutages(query.target.replace(/\D/g, ''), range), null]
  } else if (targetMode.value === 'country') {
    outageTasks = [
      getCountryASOutages(query.target.trim(), range),
      getCountryPrefixOutages(query.target.trim(), range),
    ]
  } else {
    outageTasks = [getGlobalASOutages(range), getGlobalPrefixOutages(range)]
  }

  const [featureResult, primaryResult, secondaryResult] = await Promise.allSettled([
    featureTask,
    outageTasks[0],
    outageTasks[1] || Promise.resolve([]),
  ])

  if (featureResult.status === 'fulfilled') features.value = featureResult.value
  else featureError.value = errorMessage(featureResult.reason)

  if (primaryResult.status === 'fulfilled') outagePrimary.value = primaryResult.value
  else outageError.value = errorMessage(primaryResult.reason)

  if (secondaryResult.status === 'fulfilled') outageSecondary.value = secondaryResult.value
  else outageError.value ||= errorMessage(secondaryResult.reason)
  loading.value = false
}

onMounted(load)
</script>

<template>
  <article class="page features-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">时序特征 / Feature profile</p>
        <h1>综合特征</h1>
      </div>
      <p class="page-heading-copy">
        以同一时间范围查看采集点、国家或 ASN 的报文量、路由资源量和并发中断量，缺失资源字段保持为空。
      </p>
    </header>

    <form class="feature-console" @submit.prevent="load">
      <label class="target-field">
        <span>观察目标</span>
        <input v-model="query.target" list="target-suggestions" placeholder="collector / 中国 / AS4134" />
        <datalist id="target-suggestions">
          <option value="collector">全球采集点</option>
          <option value="中国">国家：中国</option>
          <option value="美国">国家：美国</option>
          <option value="AS4134">ASN：4134</option>
        </datalist>
      </label>
      <label>
        <span>开始时间</span>
        <input v-model="query.start" type="datetime-local" />
      </label>
      <label>
        <span>结束时间</span>
        <input v-model="query.end" type="datetime-local" />
      </label>
      <button class="solid-action" type="submit">刷新剖面</button>
    </form>

    <div class="target-ledger">
      <span>ACTIVE TARGET</span>
      <strong>{{ targetLabel }}</strong>
      <small>{{ targetMode.toUpperCase() }} MODE</small>
    </div>

    <section class="chart-section">
      <div class="section-heading">
        <h2>01 / 路由报文量</h2>
        <span>announce vs withdraw</span>
      </div>
      <PageState v-if="loading" kind="loading" title="正在读取特征时间序列" />
      <PageState
        v-else-if="featureError"
        kind="error"
        title="报文特征不可用"
        :detail="featureError"
        @retry="load"
      />
      <LineChart v-else :series="messageSeries" unit="条" :height="310" />
    </section>

    <section class="chart-section">
      <div class="section-heading">
        <h2>02 / 路由资源量</h2>
        <span>prefix inventory · null ≠ zero</span>
      </div>
      <PageState v-if="loading" kind="loading" title="正在读取资源快照" />
      <PageState
        v-else-if="featureError"
        kind="error"
        title="资源特征不可用"
        :detail="featureError"
        @retry="load"
      />
      <LineChart v-else :series="resourceSeries" unit="个" :height="310" />
    </section>

    <section class="chart-section">
      <div class="section-heading">
        <h2>03 / 并发中断量</h2>
        <span>3-minute slots · active events</span>
      </div>
      <PageState v-if="loading" kind="loading" title="正在聚合中断时间槽" />
      <PageState
        v-else-if="outageError"
        kind="error"
        title="中断特征不可用"
        :detail="outageError"
        @retry="load"
      />
      <LineChart v-else :series="outageSeries" unit="起" :height="330" />
      <p class="chart-note">
        该图表示时间槽内仍处于活动状态的中断事件数，不是历史累计事件数。空槽由后端补零，静态资源缺失则保持为空。
      </p>
    </section>
  </article>
</template>

<style scoped>
.feature-console {
  display: grid;
  grid-template-columns: minmax(220px, 1.25fr) repeat(2, minmax(190px, 1fr)) 132px;
  gap: 13px;
  padding: 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.feature-console label {
  display: grid;
  gap: 7px;
}

.feature-console label span {
  color: var(--muted);
  font-size: 10px;
  font-weight: 650;
}

.feature-console input {
  width: 100%;
  min-width: 0;
  height: 38px;
  padding: 0 10px;
  color: var(--ink);
  background: #fff;
  border: 1px solid #cfd7e1;
  border-radius: 5px;
  font-size: 12px;
}

.feature-console .solid-action {
  align-self: end;
  height: 38px;
  min-height: 38px;
}

.target-ledger {
  display: grid;
  grid-template-columns: auto 1fr auto;
  align-items: center;
  gap: 18px;
  padding: 14px 18px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-left: 3px solid var(--primary);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.target-ledger span,
.target-ledger small {
  color: var(--muted);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.045em;
}

.target-ledger strong {
  color: #17212b;
  font-size: 20px;
  font-weight: 720;
}

.chart-section {
  display: grid;
  gap: 14px;
  padding: 18px;
}

.chart-note {
  margin: -2px 0 0;
  padding: 10px 12px;
  color: var(--muted);
  background: #f8fafc;
  border-left: 3px solid var(--cyan);
  font-size: 11px;
  line-height: 1.6;
}

@media (max-width: 1100px) {
  .feature-console {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }
}

@media (max-width: 620px) {
  .feature-console {
    grid-template-columns: 1fr;
    gap: 11px;
    padding: 13px;
  }

  .target-ledger {
    grid-template-columns: 1fr;
    gap: 8px;
  }

  .chart-section {
    padding: 14px;
  }
}
</style>
