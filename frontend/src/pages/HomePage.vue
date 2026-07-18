<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'
import { RouterLink, useRouter } from 'vue-router'

import { getEventCounts } from '@/api/dashboard'
import { getTopEvents } from '@/api/events'
import { getTopFeatures } from '@/api/features'
import EventTable from '@/components/EventTable.vue'
import LineChart, { type ChartSeries } from '@/components/LineChart.vue'
import PageState from '@/components/PageState.vue'
import type { CountPoint, EventRow, FeaturePoint } from '@/types/api'
import { errorMessage } from '@/utils/normalize'
import { recentRange, toBackendTime } from '@/utils/time'

const router = useRouter()
const events = ref<EventRow[]>([])
const counts = ref<CountPoint[]>([])
const features = ref<FeaturePoint[]>([])
const loading = ref(true)
const eventError = ref('')
const chartError = ref('')

const totalEvents = computed(() => counts.value.reduce((sum, point) => sum + point.count, 0))
const latestCount = computed(() => counts.value.at(-1)?.count ?? 0)
const latestObservation = computed(() => features.value.at(-1)?.time || '等待特征数据')

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

async function load() {
  loading.value = true
  eventError.value = ''
  chartError.value = ''
  const range = recentRange(24)
  const [eventResult, countResult, featureResult] = await Promise.allSettled([
    getTopEvents(),
    getEventCounts(),
    getTopFeatures('collector', {
      start_time: toBackendTime(range.start),
      end_time: toBackendTime(range.end),
    }),
  ])

  if (eventResult.status === 'fulfilled') events.value = eventResult.value
  else eventError.value = errorMessage(eventResult.reason)

  if (countResult.status === 'fulfilled') counts.value = countResult.value
  else eventError.value ||= errorMessage(countResult.reason)

  if (featureResult.status === 'fulfilled') features.value = featureResult.value
  else chartError.value = errorMessage(featureResult.reason)
  loading.value = false
}

function openEvent(event: EventRow) {
  if (!event.detailUrl) return
  void router.push({ name: 'event-detail', query: { ref: event.detailUrl } })
}

onMounted(load)
</script>

<template>
  <article class="page home-page">
    <header class="page-heading">
      <div>
        <p class="eyebrow">核心态势 / Overview</p>
        <h1>路由异常监测概览</h1>
      </div>
      <p class="page-heading-copy">
        汇总 BGP 报文变化、六类核心异常和最新事件，数据范围固定保留自 2026 年 2 月 1 日以来的发布快照。
      </p>
    </header>

    <section class="metric-ledger" aria-label="核心指标">
      <div>
        <span>近 30 日事件</span>
        <strong>{{ totalEvents.toLocaleString('zh-CN') }}</strong>
        <small>EVENT RECORDS</small>
      </div>
      <div>
        <span>最近统计日</span>
        <strong>{{ latestCount.toLocaleString('zh-CN') }}</strong>
        <small>{{ counts.at(-1)?.time || 'NO SAMPLE' }}</small>
      </div>
      <div>
        <span>核心异常类型</span>
        <strong>06</strong>
        <small>HIJACK / LEAK / OUTAGE</small>
      </div>
      <div>
        <span>最后观测</span>
        <strong class="metric-time">{{ latestObservation }}</strong>
        <small>COLLECTOR FEATURE</small>
      </div>
    </section>

    <section class="home-grid">
      <div class="home-chart dashboard-card">
        <div class="section-heading">
          <h2>采集点报文脉冲</h2>
          <RouterLink to="/features">进入特征分析 →</RouterLink>
        </div>
        <PageState
          v-if="loading"
          kind="loading"
          title="正在同步 24 小时特征"
          detail="读取采集点 ANNOUNCE 与 WITHDRAW 时序"
        />
        <PageState
          v-else-if="chartError"
          kind="error"
          title="特征数据暂不可用"
          :detail="chartError"
          @retry="load"
        />
        <LineChart v-else :series="messageSeries" unit="条" :height="330" />
      </div>

      <aside class="detection-index dashboard-card">
        <div class="section-heading">
          <h2>检测索引</h2>
          <span>06 classes</span>
        </div>
        <ol>
          <li><b>01</b><span>前缀劫持</span><small>起源 AS 偏离</small></li>
          <li><b>02</b><span>子前缀劫持</span><small>更具体前缀偏离</small></li>
          <li><b>03</b><span>路由泄漏</span><small>AS_PATH 关系异常</small></li>
          <li><b>04</b><span>前缀中断</span><small>可见性消失</small></li>
          <li><b>05</b><span>AS 中断</span><small>前缀聚合异常</small></li>
          <li><b>06</b><span>国家中断</span><small>AS 聚合异常</small></li>
        </ol>
      </aside>
    </section>

    <section class="events-card dashboard-card">
      <div class="section-heading">
        <h2>最新核心事件</h2>
        <RouterLink to="/events">查看全部事件 →</RouterLink>
      </div>
      <PageState
        v-if="loading"
        kind="loading"
        title="正在读取事件总表"
        detail="只查询六类核心异常"
      />
      <PageState
        v-else-if="eventError"
        kind="error"
        title="事件数据暂不可用"
        :detail="eventError"
        @retry="load"
      />
      <PageState v-else-if="events.length === 0" title="当前范围没有核心异常事件" />
      <EventTable v-else :events="events" compact @select="openEvent" />
    </section>
  </article>
</template>

<style scoped>
.metric-ledger {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  gap: 12px;
}

.metric-ledger > div {
  min-height: 112px;
  display: flex;
  flex-direction: column;
  justify-content: space-between;
  padding: 15px 16px;
  background: var(--paper);
  border: 1px solid var(--line);
  border-radius: var(--radius);
  box-shadow: var(--shadow-sm);
}

.metric-ledger span,
.metric-ledger small {
  color: var(--muted);
  font-size: 9px;
  font-weight: 650;
  letter-spacing: 0.035em;
}

.metric-ledger strong {
  color: #17212b;
  font-size: 34px;
  font-weight: 720;
  line-height: 1;
  letter-spacing: -0.035em;
}

.metric-ledger .metric-time {
  font: 650 13px/1.35 var(--mono);
  letter-spacing: -0.02em;
}

.home-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.7fr) minmax(280px, 0.72fr);
  gap: 16px;
}

.home-chart,
.detection-index {
  display: grid;
  align-content: start;
  gap: 14px;
  padding: 18px;
}

.detection-index ol {
  list-style: none;
  margin: 0;
  padding: 0;
  border: 1px solid var(--line);
  border-radius: 6px;
}

.detection-index li {
  display: grid;
  grid-template-columns: 32px 1fr;
  align-items: center;
  gap: 10px;
  padding: 11px 12px;
  border-bottom: 1px solid var(--line);
}

.detection-index li:last-child {
  border-bottom: 0;
}

.detection-index b {
  color: var(--signal);
  font: 700 9px/1 var(--mono);
}

.detection-index span {
  color: #344054;
  font-size: 12px;
  font-weight: 650;
}

.detection-index small {
  grid-column: 2;
  color: var(--muted);
  font-size: 9px;
}

.events-card {
  overflow: hidden;
  padding-top: 18px;
}

.events-card > .section-heading {
  margin: 0 18px 14px;
}

.events-card > .page-state {
  margin: 0 18px 18px;
}

@media (max-width: 1100px) {
  .metric-ledger {
    grid-template-columns: repeat(2, minmax(0, 1fr));
  }

  .home-grid {
    grid-template-columns: 1fr;
  }
}

@media (max-width: 560px) {
  .metric-ledger {
    grid-template-columns: 1fr;
  }

  .metric-ledger > div {
    min-height: 104px;
  }

  .metric-ledger strong {
    font-size: 30px;
  }

  .home-chart,
  .detection-index {
    padding: 14px;
  }
}
</style>
